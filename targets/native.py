# xpyBuild - eXtensible Python-based Build System
#
# Copyright (c) 2013 - 2017 Software AG, Darmstadt, Germany and/or its licensors
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# $Id: native.py 301527 2017-02-06 15:31:43Z matj $
#

import os, inspect, re, string, time

from buildcommon import *
from basetarget import BaseTarget
from propertysupport import defineOption
from utils.process import call
from pathsets import PathSet, BasePathSet
from buildcontext import getBuildInitializationContext
from buildexceptions import BuildException
from propertyfunctors import make_functor, Composable
from utils.fileutils import openForWrite, mkdir, deleteFile, getmtime, exists, toLongPathSafe

class __CompilersNotSpecified(object):
	def __getattr__(self, attr):
		raise Exception('Cannot use native targets until a compiler is configured by setting the native.compilers option')
defineOption('native.compilers', __CompilersNotSpecified())
defineOption('native.libs', [])
defineOption('native.libpaths', [])
defineOption('native.c.flags', None) # defaults to native.cxx.flags if not set
defineOption('native.cxx.flags', [])
defineOption('native.cxx.path', [])
defineOption('native.include', [])
defineOption('native.link.flags', [])

if isWindows():
	defineOption('native.cxx.exenamefn', FilenameStringFormatter("%s.exe"))
	defineOption('native.cxx.libnamefn', FilenameStringFormatter("%s.dll"))
	defineOption('native.cxx.staticlibnamefn', FilenameStringFormatter("%s.lib"))
	defineOption('native.cxx.objnamefn', FilenameStringFormatter("%s.obj"))
else:
	defineOption('native.cxx.exenamefn', FilenameStringFormatter("%s"))
	defineOption('native.cxx.libnamefn', FilenameStringFormatter("lib%s.so"))
	defineOption('native.cxx.staticlibnamefn', FilenameStringFormatter("lib%s.a"))
	defineOption('native.cxx.objnamefn', FilenameStringFormatter("%s.o"))

makedeplog = logging.getLogger('MakeDepend')
class CompilerMakeDependsPathSet(BasePathSet):
	"""
		Use the selection ToolChain to get a list of dependencies from a set of source files
	"""
	def __init__(self, target, src, flags=None, includes=None):
		"""
		@param target: the BaseTarget object for which this path set is being caculated

		@param src: a PathSet of source file paths

		@param flags: additional compiler flags

		@param includes: a PathSet containing a list of directory include paths, as strings or PathSets. 
		DirGeneratedByTarget must be used only if a string is concatenated onto the end of the target name. 
		"""
		BasePathSet.__init__(self)
		self.log = makedeplog
		self.target = target
		self.sources = src
		self.flags = flatten([flags]) or []
		self.includes = includes
		
	def __repr__(self):
		return "MakeDepend(%s, %s)" % (self.sources, self.flags)
	def resolveWithDestinations(self, context):
		assert False, 'CompilerMakeDependsPathSet.resolveWithDestinations should never be called'
		
	def clean(self):
		dfile = self.target.workDir+'.makedepend'
		deleteFile(dfile)
	def _resolveUnderlyingDependencies(self, context):	
		deplist = None
		options = self.target.options # get the merged options

		dfile = toLongPathSafe(self.target.workDir+'.makedepend')
		testsources = self.sources.resolve(context)
		depsources = self.sources._resolveUnderlyingDependencies(context)

		needsRebuild = not os.path.exists(dfile)
		if needsRebuild:
			self.log.info("Rebuilding dependencies for %s because cached dependencies file does not exist (%s)" % (self.target, dfile))
		dfiletime = 0 if needsRebuild else getmtime(dfile) 
		
		for x in testsources:
			# TODO: could avoid looking up stat of this file twice
			if not exists(x):
				# can't generate any deps if some source files don't yet exist
				
				# note that when we do this, we'll be using a potentially old source file
				self.log.info("Dependency generation %s postponed to next incremental build because source file does not exist: %s" % (self.target, x))
				# TODO: should add include dirs too
				return depsources
			elif (not needsRebuild) and getmtime(x) > dfiletime:
				self.log.info("Rebuilding dependencies for %s because cached dependencies file is older than %s" % (self.target, x))
				needsRebuild = True
		
		if not needsRebuild: # read in cached makedepends
			deplist = []
			with open(dfile) as f:
				lines = f.readlines()
				header = lines[0].strip()
				lines = lines[1:]
				for d in lines:
					d = d.strip()
					if not d: continue
					if context._isValidTarget(d) or exists(toLongPathSafe(d)):
						deplist.append( (d, self) )
					else:
						needsRebuild = True
						self.log.warn("Rebuilding dependencies for %s because dependency %s is missing" % (self.target, d))
						break
			if header != str(self):
				self.log.info("Rebuilding dependencies for %s because target options have changed (%s != %s)" % (self.target, header, str(self)))
			elif not needsRebuild:
				return deplist

		# generate them again; allow other threads to execute dep checking while we 
		# do this as makedepends benefits a lot from parallelism
		startt = time.time()
		with context._dependencyCheckingEnableParallelism():
			self.log.info("*** Generating native dependencies for %s" % self.target)
			
			generatedIncludeDirs = []
			for d, _ in self.includes._resolveUnderlyingDependencies(context):
				if not d.endswith(os.sep): d += os.sep # includes are always directories
				if context._isValidTarget(d) and (d not in generatedIncludeDirs):
					generatedIncludeDirs.append(d)
			def isGeneratedIncludeFile(e):
				for generated in generatedIncludeDirs:
					if e.startswith(generated): 
						return True
				return False
			try:
				deplist = options['native.compilers'].dependencies.depends(context=context, src=testsources, options=options, 
					flags=flatten(options['native.cxx.flags']+[context.expandPropertyValues(x).split(' ') for x in self.flags]), 
					includes=flatten(self.includes.resolve(context)+[context.expandPropertyValues(x, expandList=True) for x in options['native.include']])
					)
				if IS_WINDOWS:
					deplist = [d.replace('/','\\') for d in deplist]
				
				# remove paths inside generated directories and replace with the targets themselves
				# no point using a generator given we have to materialize this to write the dfile below
				deplist = [ (d, self) for d in deplist if (not isGeneratedIncludeFile(d) and d not in testsources)] + [ (d, self) for d in generatedIncludeDirs ]
				deplist.sort() # canonical order
				
			except BuildException, e:
				if len(testsources)==1 and testsources[0] not in str(e):
					raise BuildException('Dependency resolution failed for %s: %s'%(testsources[0], e))
				raise
			
			# materialize this as a list since we need to iterate over it twice and can't do that with a generator
			depsources = list(depsources)

			mkdir(os.path.dirname(dfile))
			with openForWrite(dfile, 'wb') as f:
				assert not os.linesep in str(self)
				f.write(str(self)+os.linesep)
				for d, _ in deplist+[('', None)]+depsources: # spacer to make it easier to see what's going on - distinction between depsources and deplist
					f.write(d.encode('UTF-8')+os.linesep)
			if time.time()-startt > 5: # this should usually be pretty quick, so may indicate a real build file mistake
				self.log.warn('Dependency generation took a long time: %0.1f s to evaluate %s', time.time()-startt, self)
		
		# ideally file existence checking should happen here else (due to postponement above) it may not be possible; not done currently
		
		return deplist+depsources

class Cpp(BaseTarget):
	""" A target that compiles a C++ source file to a .o
	"""
	
	def __init__(self, object, source, includes=None, flags=None, dependencies=None, options=None):
		"""
		@param object: the object file to generate
		@param source: a (list of) source files
		@param includes: a (list of) include directories, as strings or PathSets. If specifying a subdirectory of a generated directory, use DirGeneratedByTarget. 
		@param flags: a list of additional compiler flags
		@param dependencies: a list of additional dependencies that need to be built 
		before this target
		@param options: [DEPRECATED - use .option() instead]
		"""
		self.source = PathSet(source)
		self.includes = PathSet(includes)
		self.flags = flatten([flags]) or []
		self.makedepend = CompilerMakeDependsPathSet(self, self.source, flags=self.flags, includes=self.includes)
		BaseTarget.__init__(self, object, [dependencies or [], self.source, self.makedepend])
		
		for k,v in (options or {}).items(): self.option(k, v)
		self.tags('native')
	
	def run(self, context):
		options = self.options

		mkdir(os.path.dirname(self.path))
		options['native.compilers'].cxxcompiler.compile(context, output=self.path, options=options, flags=flatten(options['native.cxx.flags']+[context.expandPropertyValues(x).split(' ') for x in self.flags]), src=self.source.resolve(context), includes=flatten(self.includes.resolve(context)+[context.expandPropertyValues(x, expandList=True) for x in options['native.include']]))

	def clean(self, context):
		self.makedepend.clean()
		BaseTarget.clean(self, context)

	def getHashableImplicitInputs(self, context):
		r = super(Cpp, self).getHashableImplicitInputs(context)
		
		# include input to makedepends, since even without running makedepends 
		# we know we're out of date if inputs have changed
		r.append('depends: '+context.expandPropertyValues(str(self.makedepend)))
		
		return r
		
class C(Cpp):
	""" A target that compiles a C source file to a .o
	"""
	
	# identical to Cpp except for actual run method
	
	def run(self, context):
		options = self.options
		mkdir(os.path.dirname(self.path))
		options['native.compilers'].ccompiler.compile(context, output=self.path,
				options=options, 
				flags=flatten((options['native.c.flags'] or options['native.cxx.flags'])+[context.expandPropertyValues(x).split(' ') for x in self.flags]), 
				src=self.source.resolve(context),
				includes=flatten(self.includes.resolve(context)+[context.expandPropertyValues(x, expandList=True) for x in options['native.include']]))
		
class Link(BaseTarget):
	""" A target that links object files to binaries
	"""
	
	def __init__(self, bin, objects, libs=None, libpaths=None, shared=False, options=None, flags=None, dependencies=None):
		"""
		@param bin: the output binary

		@param objects: a (list of) input object

		@param libs: a (list of) libraries linked against (optional) in platform-neutral format. 
		Can include list properties like '${FOO_LIB_NAMES[]}'. 

		@param libpaths: a (list of) additional library search directories (optional)

		@param shared: if true compiles to a shared object (.dll or .so) (optional, defaults to false)

		@param flags: a list of additional linker flags

		@param options: [DEPRECATED - use .option() instead]

		@param dependencies: a list of additional dependencies (targets or files)
		"""
		self.objects = PathSet(objects)
		self.libs = libs or []
		self.libpaths = PathSet(libpaths or [])
		self.shared=shared
		self.flags = flags or []
		BaseTarget.__init__(self, bin, PathSet(self.objects, (dependencies or [])))
		for k,v in (options or {}).items(): self.option(k, v)
		
		self.tags('native')
	
	def run(self, context):
		options = self.options

		mkdir(os.path.dirname(self.path))
		options['native.compilers'].linker.link(context, output=self.path,
				options=options, 
				flags=options['native.link.flags']+self.flags, 
				shared=self.shared,
				src=self.objects.resolve(context),
				libs=flatten([map(string.strip, context.expandPropertyValues(x, expandList=True)) for x in self.libs+options['native.libs'] if x]),
				libdirs=flatten(self.libpaths.resolve(context)+[context.expandPropertyValues(x, expandList=True) for x in options['native.libpaths']]))

	def getHashableImplicitInputs(self, context):
		r = super(Link, self).getHashableImplicitInputs(context)
		
		options = self.options
		r.append('libs: '+context.expandPropertyValues(str(self.libs+options['native.libs'])))
		r.append('libpaths: '+context.expandPropertyValues(str(self.libpaths)))
		r.append('native.libpaths: %s'%options['native.libpaths'])
		r.append('shared: %s, flags=%s'%(self.shared, self.flags))
		
		return r
		
class Ar(BaseTarget):
	""" A target that compiles .a files from collections of .o files
	"""
	
	def __init__(self, bin, objects):
		"""
		@param bin: the output library

		@param objects: a (list of) input objects

		"""
		self.objects = PathSet(objects)
		BaseTarget.__init__(self, bin, self.objects)
		self.tags('native')
	
	def run(self, context):
		options = self.options

		mkdir(os.path.dirname(self.path))
		options['native.compilers'].archiver.archive(context, output=self.path,
				options=options,
				src=self.objects.resolve(context))

	def getHashableImplicitInputs(self, context):
		r = super(Ar, self).getHashableImplicitInputs(context)
		
		r.append('objects: %s'%self.objects)
		
		return r
		
exename = make_functor(lambda c, i:c.mergeOptions()['native.cxx.exenamefn'](c.expandPropertyValues(i)), name='exename')
objectname = make_functor(lambda c, i:c.mergeOptions()['native.cxx.objnamefn'](c.expandPropertyValues(i)), name='objectname')
libname = make_functor(lambda c, i:c.mergeOptions()['native.cxx.libnamefn'](c.expandPropertyValues(i)), name='libname')
staticlibname = make_functor(lambda c, i:c.mergeOptions()['native.cxx.staticlibnamefn'](c.expandPropertyValues(i)), name='staticlibname')

