# xpyBuild - eXtensible Python-based Build System
#
# Copyright (c) 2013 - 2019 Software AG, Darmstadt, Germany and/or its licensors
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
# $Id: custom.py 301527 2017-02-06 15:31:43Z matj $
#

"""
Contains `xpybuild.targets.custom.CustomCommand` (and similar classes) for executing an arbitrary command line to 
produce a file or directory of output. 
"""

import os, os.path, subprocess

from xpybuild.buildcommon import *
from xpybuild.basetarget import BaseTarget, targetNameToUniqueId
from xpybuild.utils.fileutils import mkdir, deleteDir, deleteFile, normLongPath
from xpybuild.utils.process import _wait_with_timeout
from xpybuild.pathsets import PathSet, BasePathSet
from xpybuild.utils.buildexceptions import BuildException
from xpybuild.targets.copy import Copy
from xpybuild.propertysupport import defineOption

class Custom(BaseTarget): # deprecated because error handling/logging is poor and it promotes bad practices like not using options (e.g process timeout)
	"""
	@deprecated: Use `CustomCommand` instead, or a dedicated `BaseTarget` subclass. 

	A custom target that builds a single file or directory of content by executing 
	an arbitrary python functor.  

	Functor must take:
	(target path, [dependency paths], context)
	
	Tip: don't forget to ensure the target path's parent dir exists 
	using fileutils.mkdir. 
	"""
	fn = None
	cleanfn = None
	def __init__(self, target, deps, fn, cleanfn=None):
		"""
		@param target: The target file/directory that will be built

		@param deps: The list of dependencies of this target (paths, pathsets or lists)

		@param fn: The functor used to build this target

		@param cleanfn: The functor used to clean this target (optional, defaults to removing 
		the target file/dir)
		"""
		BaseTarget.__init__(self, target, deps)
		self.fn = fn
		self.cleanfn = cleanfn
		self.deps = PathSet(deps)
	def run(self, context):
		self.fn(self.path, self.deps.resolve(context), context)
	def clean(self, context):
		if self.cleanfn: self.cleanfn(self.path, context)
		BaseTarget.clean(self, context)

class ResolvePath(object):
	"""	@deprecated: Use a `PathSet` (possibly with the `joinPaths` property functor) 
	instead of this class.  
	
	A wrapper around a string in a command line that indicates it is 
	a path an should be resolved (expanded, normalized, possibly made relative 
	to the target dir) when the command is executed.
	
	If the specified path resolves to more than one item then an exception is 
	thrown unless the pathsep argument is specified. 
	
	"""
	def __init__(self, path):
		"""
		@param path: The path to expand, which is a string. 
		"""
		self.path = path
	def __repr__(self): 
		""" Returns a string including this class name and the path """
		return 'ResolvePath<%s>'%self.path
	def resolve(self, context, baseDir):
		""" Resolves the path using the specified context and baseDir """
		return context.getFullPath(self.path, defaultDir=baseDir)

defineOption('CustomCommand.outputHandlerFactory', None) 

class CustomCommand(BaseTarget):
	"""
	A custom target that builds a single file or directory of content by running a 
	specified command line process. 
	"""

	class __CustomCommandSentinel(object): 
		def __init__(self, name): self.name = name
		def __repr__(self): return 'CustomCommand.'+name
	
	TARGET = __CustomCommandSentinel('TARGET')
	"""
	A special value that can be used in the ``command`` argument and is resolved to the output path of this target. 
	"""

	DEPENDENCIES = __CustomCommandSentinel('DEPENDENCIES')
	"""
	A special value that can be used in the ``command`` argument and is resolved to a list of this target's dependencies. 
	"""
	
	def __init__(self, target, command, dependencies, cwd=None, redirectStdOutToTarget=False, env=None, stdout=None, stderr=None):
		"""
		The command line *must* not reference any generated paths unless they are 
		explicitly listed in deps. 
		
		Supported target options include:
		
		  - ``.option("process.timeout")`` to control the maximum number of seconds the command can 
		    run before being cancelled. 
		  - ``.option("common.processOutputEncodingDecider")`` to determine the encoding 
		    used for reading stdout/err (see `xpybuild.utils.process.defaultProcessOutputEncodingDecider`). 
		  - ``.option("CustomCommand.outputHandlerFactory")`` to replace the default behaviour 
		    for detecting errors (which is just based on zero/non-zero exit code) and logging stdout/err with 
		    a custom `xpybuild.utils.outputhandler.ProcessOutputHandler`. The additional 
		    options described on `ProcessOutputHandler` can also be used with this target. 

		@param target: the file or directory to be built. Will be cleaned, and its parent dir created, 
			before target runs. 
		
		@param dependencies: an optional list of dependencies; it is essential that ALL dependencies required by 
			this command and generated by the build processare explicitly listed here, in addition to any 
			files/directories used by this command that might change between builds. 
		
		@param command: a function or a list. 
		
			If command is a list, items may be:
		
				- a string (which will be run through expandPropertyValues prior to execution); 
				  must not be used for representing arguments that are paths
				
				- a `PathSet` (which must resolve to exactly one path - see `joinPaths` 
				  property functor if multiple paths are required). Any PathSets used in 
				  the arguments should usually be explicitly listed in dependencies too, 
				  especially if they are generated by another part of this build. 
				
				- a property functor such as joinPaths (useful for constructing 
				  Java classpaths), basename, etc
				
				- an arbitrary function taking a single context argument
				
				- `CustomCommand.TARGET` - a special value that is resolved to the 
				  output path of this target
				
				- `CustomCommand.DEPENDENCIES` - a special value that is resolved to 
				  a list of this target's dependencies
				
				- [deprecated] a ResolvePath(path) object, indicating a path that should be 
				  resolved and resolved at execution time (this is equivalent 
				  to using a PathSet, which is probably a better approach). 
			
			If command is a function, must have 
			signature ``(resolvedTargetDirPath, resolvedDepsList, context)``, and 
			return the command line as a list of strings. resolvedDepsList will be an 
			ordered, flattened list of resolved paths from deps. 
			
			Command lines MUST NOT depend 
			in any way on the current source or output directory, always use 
			a PathSet wrapper around such paths. 
				
		@param cwd: the working directory to run it from (almost always this should be 
			left blank, meaning use output dir)
		
		@param env: a dictionary of environment overrides, or a function that 
			returns one given a context. Values in the dictionary will 
			be expanded using the same rules as for the command (see above). 
			Consider using `xpybuild.propertysupport.joinPaths` for environment variables 
			containing a list of paths. 
		
		@param redirectStdOutToTarget: usually, any stdout is treated as logging 
			and the command is assumed to create the target file itself, but 
			set this to True for commands where the target file contents are 
			generated by the stdout of the command being executed. 
			
		@param stdout: usually a unique name is auto-generated for .out for this target, but 
			use this if required to send output to a specific location. 
		
		@param stderr: usually a unique name is auto-generated for .err for this target, but 
			use this if required to send output to a specific location. 
		"""
		BaseTarget.__init__(self, target, dependencies)
		
		self.command = command
		self.cwd = cwd
		self.deps = PathSet(dependencies)
		self.redirectStdOutToTarget = redirectStdOutToTarget
		if redirectStdOutToTarget and isDirPath(target): raise BuildException('Cannot set redirectStdOutToTarget and specify a directory for the target name - please specify a file instead: %s'%target)
		self.env = env
		self.stdout, self.stderr = stdout, stderr
		
		if stdout and redirectStdOutToTarget:
			raise BuildException('Cannot set both redirectStdOutToTarget and stdout')

	def _resolveItem(self, x, context):
		if x == self.DEPENDENCIES: return self.deps.resolve(context)
		if x == self.TARGET: x = self.path
		if isinstance(x, str): return context.expandPropertyValues(x)
		if hasattr(x, 'resolveToString'): return x.resolveToString(context) # supports Composables too
		if isinstance(x, BasePathSet): 
			result = x.resolve(context)
			if len(result) != 1:
				raise BuildException('PathSet for custom command must resolve to exactly one path not %d (or use joinPaths): %s'%(len(result), x))
			return result[0]
		if isinstance(x, ResolvePath): return x.resolve(context, self.baseDir)
		if callable(x): return x(context)
		raise Exception('Unknown custom command input type %s: %s'%(x.__class__.__name__, x))
		
	def _resolveCommand(self, context):
		if callable(self.command):
			self.command = self.command(self.path, self.deps.resolve(context), context)
		assert not isinstance(self.command, str) # must be a list of strings, not a string
			
		self.command = flatten([self._resolveItem(x, context) for x in self.command])
		self.command[0] = normLongPath(self.command[0])
		return self.command
	
	def getHashableImplicitInputs(self, context):
		return super(CustomCommand, self).getHashableImplicitInputs(context) + self._resolveCommand(context)

	def run(self, context):
		if self.cwd: self.cwd = context.getFullPath(self.cwd, self.baseDir)
		if isDirPath(self.path):
			mkdir(self.path)
			cwd = self.cwd or self.path
		else:
			mkdir(os.path.dirname(self.path))
			cwd = self.cwd or self.workDir
		mkdir(self.workDir)
		
		cmd = self._resolveCommand(context)
		
		# this location is a lot easier to find than the target's workdir
		logbasename = os.path.normpath(context.getPropertyValue('BUILD_WORK_DIR')+'/CustomCommandOutput/'+os.path.basename(cmd[0])+"."+targetNameToUniqueId(self.name))
		
		
		stdoutPath = context.getFullPath(self.path if self.redirectStdOutToTarget else (self.stdout or logbasename+'.out'), defaultDir='${BUILD_WORK_DIR}/CustomCommandOutput/')
		stderrPath = context.getFullPath(self.stderr or logbasename+'.err', defaultDir='${BUILD_WORK_DIR}/CustomCommandOutput/')

		self.log.info('Building %s by executing command line: %s', self.name, ''.join(['\n\t"%s"'%x for x in cmd]))
		if self.cwd: self.log.info('Building %s from working directory: %s', self.name, self.cwd) # only print if overridden
		env = self.env or {}
		if env:
			if callable(env):
				env = env(context)
			else:
				env = {k: None if None == env[k] else self._resolveItem(env[k], context) for k in env}
			self.log.info('Environment overrides for %s are: %s', self.name, ''.join(['\n\t"%s=%s"'%(k, env[k]) for k in env]))
		for k in os.environ:
			if k not in env: env[k] = os.getenv(k)

		for k in list(env.keys()):
			if None == env[k]:
				del env[k]
		self.log.info('Output from %s will be written to "%s" and "%s"', self.name, 
			stdoutPath, 
			stderrPath)
		
				
		if not os.path.exists(cmd[0]) and not (IS_WINDOWS and os.path.exists(cmd[0]+'.exe')):
			raise BuildException('Cannot run command because the executable does not exist: "%s"'%(cmd[0]), location=self.location)

		encoding = self.options['common.processOutputEncodingDecider'](context, cmd[0])
		handler = self.options['CustomCommand.outputHandlerFactory']
		if handler:
			handler = handler(str(self), options=self.options)

		try:
			success=False
			rc = None
			try:
				# maybe send output to a file instead
				mkdir(os.path.dirname(logbasename))
				with open(stderrPath, 'wb') as fe: # can't use openForWrite with subprocess
					with open(stdoutPath, 'wb') as fo:
						process = subprocess.Popen(cmd, 
							stderr=fe, 
							stdout=fo,
							cwd=cwd, 
							env=env)

						rc = _wait_with_timeout(process, '%s(%s)'%(self.name, os.path.basename(cmd[0])), self.options['process.timeout'], False)
						success = rc == 0
				
			finally:
				try:
					if os.path.getsize(stderrPath) == 0 and not self.stderr: deleteFile(stderrPath, allowRetry=True)
					if not self.redirectStdOutToTarget and os.path.getsize(stdoutPath) == 0 and not self.stdout: deleteFile(stdoutPath, allowRetry=True)
				except Exception as e:
					# stupid windows, it passes understanding
					self.log.info('Failed to delete empty .out/.err files (ignoring error as its not critical): %s', e)
					
				#if not os.listdir(self.workDir): deleteDir(self.workDir) # don't leave empty work dirs around
	
				mainlog = '<command generated no output>'
				
				logMethod = self.log.info if success else self.log.error
				
				if (handler or not self.redirectStdOutToTarget) and os.path.isfile(stdoutPath) and os.path.getsize(stdoutPath) > 0:
					if handler:
						with open(stdoutPath, 'r', encoding=encoding, errors='replace') as f:
							for l in f: handler.handleLine(l, isstderr=False)
					elif os.path.getsize(stdoutPath) < 15*1024:
						logMethod('Output from %s stdout is: \n%s', self.name, open(stdoutPath, 'r', encoding=encoding, errors='replace').read().replace('\n', '\n\t'))
					mainlog = stdoutPath
					if not success: context.publishArtifact('%s stdout'%self, stdoutPath)
				if os.path.isfile(stderrPath) and os.path.getsize(stderrPath) > 0:
					if handler:
						with open(stderrPath, 'r', encoding=encoding, errors='replace') as f:
							for l in f: handler.handleLine(l, isstderr=True)
					elif os.path.getsize(stderrPath) < 15*1024:
						logMethod('Output from %s stderr is: \n%s', self.name, open(stderrPath, 'r', encoding=encoding, errors='replace').read().replace('\n', '\n\t'))
					mainlog = stderrPath # take precedence over stdout
					if not success: context.publishArtifact('%s stderr'%self, stderrPath)
			
			if handler:
				handler.handleEnd(returnCode=rc)
			elif rc != None and rc != 0 and not handler:
				raise BuildException('%s command failed with error code %s; see output at "%s"'%(os.path.basename(cmd[0]), rc, mainlog), location=self.location)
		finally:
			pass
		
		# final sanity check
		if not os.path.exists(self.path): 
			raise BuildException('%s command failed to create the target files (but returned no error code); see output at "%s"'%(os.path.basename(cmd[0]), mainlog), location=self.location)
		if (not os.listdir(self.path)) if isDirPath(self.path) else (not os.path.isfile(self.path)): 
			raise BuildException('%s created the wrong type of output on the file system (please check that trailing "/" is used if and only if a directory output is intended)'%self, location=self.location)
		
class CustomCommandWithCopy(CustomCommand, Copy):
	"""
	A custom target that builds a directory of content by running a 
	specified command line process, but unlike the normal CustomCommand 
	also copies one or more files into the output directory before running the 
	specified command. 
	
	For advanced cases only - usually it's best to find a way to explicitly 
	separate the target input and output and use a normal CustomCommand - but 
	this target exists for badly written tools that are only able to do 
	in-place modifications on a directory. 
	"""
	
	def __init__(self, target, command, dependencies, copySrc, cwd=None, redirectStdOutToTarget=False, env=None, **kwargs):
		"""
		@param target: the target
		@param command: see CustomCommand for details
		@param dependencies: an explicit list of any dependencies (other than copySrc) 
		that are required by the command, including static resources and 
		other targets generated by the build. This is ESSENTIAL for 
		reliable building. 
		"""
		assert isDirPath(target), 'This target can only be used for directories (ending in /)'
		copySrc = PathSet(copySrc)
		CustomCommand.__init__(self, target, command, dependencies=[dependencies, copySrc], cwd=cwd, redirectStdOutToTarget=redirectStdOutToTarget, env=env, **kwargs)
		# can't call Copy.__init__ without introducing a duplicate target
		# but use the same name used by Copy so we can call run successfully later
		self.src = copySrc
		self.mode = None
	
	def run(self, context):
		# first do what Copy would have done
		Copy.run(self, context)
		
		# then custom command
		CustomCommand.run(self, context)
