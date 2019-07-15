# xpyBuild - eXtensible Python-based Build System
#
# This class is responsible for working out what tasks need to run, and for 
# scheduling them
#
# Copyright (c) 2013 - 2017, 2019 Software AG, Darmstadt, Germany and/or its licensors
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
# $Id: buildtarget.py 301527 2017-02-06 15:31:43Z matj $
#

import traceback, os, time
import io
import difflib
from stat import S_ISREG, S_ISDIR # fast access for these is useful

from basetarget import BaseTarget
from buildcommon import *
from threading import Lock
from buildexceptions import BuildException
from utils.fileutils import deleteFile, mkdir, openForWrite, getmtime, exists, isfile, isdir, toLongPathSafe, getstat

import logging
log = logging.getLogger('scheduler.targetwrapper')

class TargetWrapper(object):
	"""
		Internal wrapper for a target which contains all the state needed by the 
		scheduler during builds. 
	"""
	
	__slots__ = 'target', 'path', 'name', 'isDirPath', 'lock', 'depcount', '__targetdeps', '__nontargetdeps', '__rdeps', '__isdirty', '__implicitInputs', '__implicitInputsFile', 'stampfile', 'effectivePriority', '__scheduler'
	
	# flags
	DEP_IS_DIR_PATH = 2**1
	
	DEP_SKIP_EXISTENCE_CHECK = 2**2
	"""Flag for dependencies from a pathset where there's no point checking the existence of 
	dependencies because they're already known to be present - e.g. FindPaths. """
	
	def __init__(self, target, scheduler):
		"""
			Create a TargetWrapper from a target. This target has an internal lock
			which is taken for some of the functions
		"""
		self.target = target
		self.path = target.path
		self.name = target.name
		self.isDirPath = isDirPath(target.name)

		self.lock = Lock()

		self.__scheduler = scheduler

		self.depcount = 0
		"""Counts the number of outstanding target dependencies yet to be built
		"""

		self.__rdeps = []
		"""A list of TargetWrappers that depend on this one"""

		self.__isdirty = False
		
		self.__implicitInputs = None

		self.__targetdeps = None
		""" A list of TargetWrappers that this one depends upon. 
		Can only be used after resolveUnderlyingDependencies has been called. """
		
		self.__nontargetdeps = None
		""" A sorted list of dependencies that aren't targets. Each item is a tuple
		(longpathsafepath, dep flags, pathset)
		
		longpathsafepath is a unicode string on Windows (on Python 3: also linux)
		
		flags includes: DEP_IS_DIR_PATH, DEP_SKIP_EXISTENCE_CHECK, DEP_SHORTCUT_UPTODATE_CHECK
		
		Can only be used after resolveUnderlyingDependencies has been called. 
		"""
		
		self.__implicitInputsFile = self.__getImplicitInputsFile()
		if self.isDirPath: 
			self.stampfile = self.__implicitInputsFile # might as well re-use this for dirs
		else:
			self.stampfile = self.target.path

		self.effectivePriority = target.getPriority() # may be mutated to an effective priority

	def __hash__ (self): return hash(self.target) # delegate
	
	def __str__(self): return '%s'%self.target # display string for the underlying target
	def __repr__(self): return 'TargetWrapper.%s'%str(self)
	
	def __getattr__(self, name):
		# note: getattr is very inefficient so don't use this for anything performance-critical

		# sometimes a TargetWrapper is passed to BuildException which calls .location on it
		if name == 'location': return self.target.location
				
		raise AttributeError('Unknown attribute %s' % name)
	
	def __getImplicitInputsFile(self):
		x = self.target.workDir.replace('\\','/').split('/')
		# relies on basetarget._resolveTargetPath having been called
		return '/'.join(x[:-1])+'/implicit-inputs/'+x[-1]+'.txt' # since workDir is already unique (but don't contaminate work dir by putting this inside it)
	
	def __getImplicitInputs(self, context):
	
		# this is typically called in either uptodate or run, but never during dependency resolution 
		# since we don't have all our inputs yet at that point
		if self.__implicitInputs is not None: return self.__implicitInputs

		# we could optimize this by not writing the file if all the 
		# dependencies are explicit i.e. no FindPathSets are present

		# list is already sorted (in case of determinism problems with filesystem walk order)
		# take a copy here since it is used from other places
		x = [wrapper.path for wrapper in self.__targetdeps] + [abspath for abspath,flags,pathset in self.__nontargetdeps]

		# since this is meant to be a list of lines, normalize with a split->join
		# also make any non-linesep \r or \n chars explicit to avoid confusion when diffing
		x += [x.replace('\r','\\r').replace('\n','\\n') for x in os.linesep.join(self.target.getHashableImplicitInputs(context)).split(os.linesep)]

		if isinstance(x, unicode): x = x.encode('utf-8') # TODO: remove when we switch to Python 3
		self.__implicitInputs = x
		return x

	def getTargetDependencies(self):
		"""
		Get a list of this object's dependencies that are targets, as TargetWrapper objects. 
		
		Can only be called after resolveUnderlyingDependencies. 

		Do not modify the returned list.
		"""
		self.resolveUnderlyingDependencies() # in case not yet done
		return self.__targetdeps
	
	def resolveUnderlyingDependencies(self):
		"""
			Calls through to the wrapped target, which does the expansion/replacement
			of dependency strings here.
			
			Populates the list of target deps and non target deps
			
			Idempotent. Not safe for concurrent access without locking.
		"""		
		if self.__nontargetdeps is not None: return # already called, maybe while changing priorities

		scheduler = self.__scheduler
		context = scheduler.context

		targetdeps = {} # path:instance (we use a dict for de-duplication)
		""" A list of TargetWrapper objects for the targets this one depends upon"""
		nontargetdeps = []
		
		for abspath, pathset in self.target._resolveUnderlyingDependencies(context):
			# TODO: should we canonicalize the abspath? e.g. for capitalization
			try:
				dtargetwrapper = scheduler.targetwrappers[abspath]
			except KeyError:
				# non target dep

				flags = 0
				if isDirPath(abspath): flags |= TargetWrapper.DEP_IS_DIR_PATH
				
				if pathset._skipDependenciesExistenceCheck:
					flags |= TargetWrapper.DEP_SKIP_EXISTENCE_CHECK
				
				# convert to long path at this point, so we know later checks will work; 
				# unlike targetdeps, nontargetdeps are sometimes deeply nested
				abspath = toLongPathSafe(abspath)
				nontargetdeps.append( (abspath, flags, pathset) )
			else: 
				if abspath in targetdeps: continue # avoid adding dups
				targetdeps[abspath] = dtargetwrapper
				dtargetwrapper.__rdep(self)

		# add additional dependencies from target groups of our deps
		if len(targetdeps) > 0:
			for t in list(targetdeps.values()):
				try:
					targetsingroup = context.init._targetGroups[t.path]
				except KeyError: pass
				else:
					for groupmembertarget in targetsingroup: # these are BaseTarget instances
						if groupmembertarget == t.target: continue
						groupmembertargetpath = groupmembertarget.path
						if groupmembertargetpath in targetdeps: continue
						targetdeps[groupmembertargetpath] = scheduler.targetwrappers[groupmembertargetpath]
						scheduler.targetwrappers[groupmembertargetpath].__rdep(self)

		if self.path in targetdeps: 
			del targetdeps[self.path]

		self.depcount = len(targetdeps)
		
		# sort for deterministic order (as there are some sets and dicts involved)
		nontargetdeps.sort(key=lambda (path, flags, pathset): path)
		self.__nontargetdeps, self.__targetdeps = nontargetdeps, sorted(targetdeps.values(), key=lambda wrapper: wrapper.name)
	
	def checkForNonTargetDependenciesUnderOutputDirs(self):
		"""
		Iterate over the dependencies that are not targets and return 
		the name of one that's under an output dir (suggests a missing target 
		dep), or None. 
		"""
		for dpath, flags, pathset in self.__nontargetdeps:
			for outdir in self.__scheduler.context.getTopLevelOutputDirs():
				if dpath.startswith(outdir):
					raise BuildException('Target %s depends on output %s which is implicitly created by some other directory target - please use DirGeneratedByTarget to explicitly name the directory target that it depends on'%(self, dpath), 
					location=self.target.location) # e.g. FindPaths(DirGeneratedByTarget('${OUTPUT_DIR}/foo/')+'bar/') # or similar
		
	def findMissingNonTargetDependencies(self):
		"""
		Iterate over the dependencies that are not targets and check 
		the files and dirs exist, and for directories, have the correct 
		trailing slash. Returns the path of first one that's missing or None. 
		
		"""
		
		for dpath, flags, pathset in self.__nontargetdeps:
			dnameIsDirPath = (flags & TargetWrapper.DEP_IS_DIR_PATH)!=0
			
			if (flags & TargetWrapper.DEP_SKIP_EXISTENCE_CHECK)!=0:
				# don't bother stat-ing the file if we know it's present e.g. for FindPaths
				continue
			
			dstat = getstat(dpath)
			# TODO: optimization, could compute latest file here too (i.e. only do uptodate checking for FindPaths items)
			
			if dstat is False or not ( (dnameIsDirPath and S_ISDIR(dstat.st_mode)) or (not dnameIsDirPath and S_ISREG(dstat.st_mode)) ):
				# just before we throw the exception, check it's not some other weird type of thing
				assert not os.path.exists(dpath), dpath
				return dpath
		return None
		
	def decrement(self):
		"""
			Decrements the number of outstanding dependencies to be built
			and returns the new total.
			Holds the object lock
		"""
		depcount = 0;
		with self.lock:
			self.depcount = self.depcount - 1
			depcount = self.depcount
		return depcount
	def dirty(self):
		"""
			Marks the object as explicitly dirty to avoid doing uptodate checks
			Holds the object lock
			
			Returns the previous value of __isdirty, i.e. True if this was a no-op. 
		"""
		with self.lock:
			r = self.__isdirty
			self.__isdirty = True
			return r
			
	def __rdep(self, targetwrapper):
		"""
			Adds a reverse dependency to this target
			Holds the object lock
		"""
		with self.lock:
			self.__rdeps.append(targetwrapper)
	def rdeps(self):
		"""
			Returns the list of reverse target dependencies as TargetWrapper objects.
			Holds the object lock
		"""
		with self.lock:
			return self.__rdeps

	def uptodate(self, context, ignoreDeps): 
		"""
			Checks whether the target needs to be rebuilt.
			Returns true if the target is up to date and does not need a rebuild
			Holds the object lock
			
			Called during the main build phase, after the dependency resolution phase
		"""
		with self.lock:
			log.debug('Up-to-date check for %s', self.name)
			
			if self.__isdirty: 
				# no need to log at info, will already have been done when it was marked dirty
				log.debug('Up-to-date check: %s has been marked dirty', self.name)
				return False

			if not exists(self.path):
				log.debug('Up-to-date check: %s must be built because file does not exist: "%s"', self.name, self.path)
				self.__isdirty = True # make sure we don't log this again
				return False
			
			if ignoreDeps: return True
			
			if not isfile(self.stampfile): # this is really an existence check, but if we have a dir it's an error so ignore
				# for directory targets
				log.info('Up-to-date check: %s must be rebuilt because stamp file does not exist: "%s"', self.name, self.stampfile)
				return False
			
			# assume that by this point our explicit dependencies at least exist, so it's safe to call getHashableImplicitDependencies
			implicitInputs = self.__getImplicitInputs(context)
			
			# read implicit inputs file
			if implicitInputs or self.isDirPath:
				# this is to cope with targets that have implicit inputs (e.g. globbed pathsets); might as well use the same mechanism for directories (which need a stamp file anyway)
				if not exists(self.__implicitInputsFile):
					log.info('Up-to-date check: %s must be rebuilt because implicit inputs/stamp file does not exist: "%s"', self.name, self.__implicitInputsFile)
					return False
				with io.open(toLongPathSafe(self.__implicitInputsFile), 'rb') as f:
					latestImplicitInputs = f.read().split(os.linesep)
					if latestImplicitInputs != implicitInputs:
						maxdifflines = int(os.getenv('XPYBUILD_IMPLICIT_INPUTS_MAX_DIFF_LINES', '30'))/2
						added = ['+ %s'%x for x in implicitInputs if x not in latestImplicitInputs]
						removed = ['- %s'%x for x in latestImplicitInputs if x not in implicitInputs]
						# the end is usually more informative than beginning
						if len(added) > maxdifflines: added = ['...']+added[len(added)-maxdifflines:] 
						if len(removed) > maxdifflines: removed = ['...']+removed[len(removed)-maxdifflines:]
						if not added and not removed: added = ['N/A']
						log.info(u'Up-to-date check: %s must be rebuilt because implicit inputs file has changed: "%s"\n\t%s\n', self.name, self.__implicitInputsFile, 
							'\n\t'.join(
								['previous build had %d lines, current build has %d lines'%(len(latestImplicitInputs), len(implicitInputs))]+removed+added
							).replace(u'\r',u'\\r\r'))
						return False
					else:
						log.debug("Up-to-date check: implicit inputs file contents has not changed: %s", self.__implicitInputsFile)
			else:
				log.debug("Up-to-date check: target has no implicitInputs data: %s", self)
			
			
			# NB: there shouldn't be any file system errors here since we've checked for the existence of deps 
			# already in _expand_deps; if this happens it's probably a build system bug
			stampmodtime = getmtime(self.stampfile)

			def isNewer(path):
				# assumes already long path safe
				pathmodtime = getmtime(path)
				if pathmodtime <= stampmodtime: return False
				if pathmodtime-stampmodtime < 1: # such a small time gap seems dodgy
					log.warn('Up-to-date check: %s must be rebuilt because input file "%s" is newer than "%s" by just %0.1f seconds', self.name, path, self.stampfile, pathmodtime-stampmodtime)
				else:
					log.info('Up-to-date check: %s must be rebuilt because input file "%s" is newer than "%s" (by %0.1f seconds)', self.name, path, self.stampfile, pathmodtime-stampmodtime)
				return True

			# things to check:
			for dtargetwrapper in self.__targetdeps:
				if not dtargetwrapper.isDirPath:
					f = dtargetwrapper.path # might have an already built target dependency which is still newer
				else:
					# special case directory target deps - must use stamp file not dir, to avoid re-walking 
					# the directory needlessly, and possibly making a wrong decision if the dir pathset is 
					# from a filtered pathset
					f = dtargetwrapper.stampfile
				if isNewer(toLongPathSafe(f)): return False

			for abslongpath, depflags, pathset in self.__nontargetdeps: 
				if depflags & TargetWrapper.DEP_IS_DIR_PATH == 0: # ignore directories as timestamp is meaningless
					if isNewer(abslongpath): return False
		return True


	def updatePriority(self):
		"""
		Push the priority from this target down to its dependencies. 
		
		This method is not thread-safe and should be called from only one thread 
		before the build phase begins. 
		"""
		deps = self.getTargetDependencies()
		if len(deps)>0:
			targetpriority = self.effectivePriority
			for dt in deps:
				if targetpriority > dt.effectivePriority:
					log.debug("Setting priority=%s on target %s", targetpriority, dt.name)
					dt.effectivePriority = targetpriority
					dt.updatePriority()
							
	def run(self, context):
		"""
			Calls the wrapped run method
		"""
		implicitInputs = self.__getImplicitInputs(context)
		if implicitInputs or self.isDirPath:
			deleteFile(self.__implicitInputsFile)
		
		self.target.run(context)
		
		# if target built successfully, record what the implicit inputs were to help with the next up to date 
		# check and ensure incremental build is correct
		if implicitInputs or self.isDirPath:
			log.debug('writing implicitInputsFile: %s', self.__implicitInputsFile)
			mkdir(os.path.dirname(self.__implicitInputsFile))
			with openForWrite(toLongPathSafe(self.__implicitInputsFile), 'wb') as f:
				f.write(os.linesep.join(implicitInputs))
		
	def clean(self, context):
		"""
			Calls the wrapped clean method
		"""
		try:
			deleteFile(self.__implicitInputsFile)
		except Exception:
			time.sleep(10.0)
			deleteFile(self.__implicitInputsFile)
		self.target.clean(context)

	def internal_clean(self, context):
		"""
			Calls the BaseTarget clean, not the target-specific clean
		"""
		try:
			deleteFile(self.__implicitInputsFile)
		except Exception:
			time.sleep(10.0)
			deleteFile(self.__implicitInputsFile)
		BaseTarget.clean(self.target, context)
