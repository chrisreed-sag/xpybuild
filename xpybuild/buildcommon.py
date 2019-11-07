# xpyBuild - eXtensible Python-based Build System
#
# This module holds definitions that are used throughout the build system, and 
# typically all names from this module will be imported. 
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
# $Id: buildcommon.py 301527 2017-02-06 15:31:43Z matj $
#

import traceback, os, sys, locale, inspect, io
import re
import platform

import logging
# do NOT define a 'log' variable here or targets will use it by mistake

def __getXpybuildVersion():

	try:
		with open(os.path.join(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))), "XPYBUILD_VERSION")) as f:
			return f.read().strip()
	except Exception:
		raise
		return "<unknown>"
XPYBUILD_VERSION: str = __getXpybuildVersion()
"""The current xpybuild version."""


def include(file):
	""" Parse and register the targets and properties in in the specified 
	xpybuild.py file. 
	
	Targets should only be defined in files included using this method, 
	not using python import statements. 
	
	@param file: a path relative to the directory containing this file. 
	"""

	from xpybuild.buildcontext import getBuildInitializationContext
	from xpybuild.utils.buildfilelocation import BuildFileLocation

	file = getBuildInitializationContext().expandPropertyValues(file)

	assert file.endswith('.xpybuild.py') # enforce recommended naming convention
	
	filepath = getBuildInitializationContext().getFullPath(file, os.path.dirname(BuildFileLocation._currentBuildFile[-1]))
	
	BuildFileLocation._currentBuildFile.append(filepath) # add to stack of files being parsed
	
	namespace = {}
	exec(compile(open(filepath, "rb").read(), filepath, 'exec'), namespace, namespace)
	
	del BuildFileLocation._currentBuildFile[-1]
	
	return namespace


def normpath(path):
	"""
	.. private: This is deprecated in favour of fileutils.normLongPath and hidden from documentation to avoid polluting the docs. 

	Normalizes the specified file or dir path to remove ".." sequences and 
	differences in the capitalization of Windows drive letters. 
	
	Does not add Windows long-path safety or absolutization. 
	
	Leaves in place any  trailing platform-appropriate character to indicate 
	directory if appropriate.
	"""
	path = os.path.normpath(path)+(os.path.sep if isDirPath(path) else '')
	
	# normpath does nothing to normalize case, and windows seems to be quite random about upper/lower case 
	# for drive letters (more so than directory names), with different cmd prompts frequently using different 
	# capitalization, so normalize at least that bit, to prevent spurious rebuilding from different prompts
	if len(path)>2 and IS_WINDOWS and path[1] == ':': 
		path = path[0].lower()+path[1:]
			
	return path

IS_WINDOWS: bool = platform.system()=='Windows'
""" A boolean that specifies whether this is Windows or some other operating system. """
# (we won't want constants for every possible OS here, but since there is so much conditionalization between 
# windows and unix-based systems, much of it on the critical path, it is worthwhile having a constant for this). 

if IS_WINDOWS:
	def isWindows():
		""" Returns True if this is a windows platform. 
		@deprecated: Use the IS_WINDOWS constant instead. 
		"""
		return True
else:
	def isWindows():
		""" Returns True if this is a windows platform. 
		@deprecated: Use the IS_WINDOWS constant instead. 
		"""
		return False

_stdoutEncoding = None
try:
	_stdoutEncoding = sys.stdout.encoding or locale.getpreferredencoding() # stdout encoding will be None unless in a terminal
except:
	pass # probably in epydoc

def getStdoutEncoding() -> str: 
	""" Returns the most likely encoding used by subprocesses, based on 
	whether the build is running in a console, etc 
	which is typically what should be used for converting byte strings from 
	subprocesses to python unicode.
	"""
	return _stdoutEncoding

def defineAtomicTargetGroup(*targets):
	""" The given targets must all be built before anything which depends on any of those targets.
	
	Returns the flattened list of targets. 
	"""
	
	from xpybuild.buildcontext import getBuildInitializationContext
	targets = flatten(targets)
	getBuildInitializationContext().defineAtomicTargetGroup(targets)
	return targets

def requireXpyBuildVersion(version: str):
	""" Checks that this xpybuild is at least a certain version number. """
	from xpybuild.utils.stringutils import compareVersions
	if compareVersions(XPYBUILD_VERSION, version) < 0: raise Exception("This build file requires xpyBuild at least version "+version+" but this is xpyBuild "+XPYBUILD_VERSION)

def registerPreBuildCheck(fn):
	""" Defines a check which will be called after any clean but before any build actions take place.
	    fn should be a functor that takes a context and raises a BuildException if the check fails. """
	from buildcontext import getBuildInitializationContext
	getBuildInitializationContext().registerPreBuildCheck(fn)

class StringFormatter(object):
	""" A simple named functor for applying a %s-style string format, useful 
	in situations where a function is needed to add a suffix/prefix for the 
	value of an option. 
	"""
	def __init__(self, formatstring):
		self.fmt = formatstring
	def __repr__(self):
		return 'StringFormatter<"%s">'%self.fmt
	def __call__(self, *args, **kwargs):
		assert not kwargs
		assert len(args)==1
		return self.fmt % args[0]
	
class FilenameStringFormatter(object):
	""" A simple named functor for applying a %s-style string format. 
		 Formatter is just applied to the basename part of the filename,
		 the dirname part is preserved as-is.
	"""
	def __init__(self, formatstring):
		self.fmt = formatstring
	def __repr__(self):
		return 'FilenameStringFormatter<"%s">'%self.fmt
	def __call__(self, *args, **kwargs):
		assert not kwargs
		assert len(args)==1
		return os.path.join(os.path.dirname(args[0]), self.fmt % os.path.basename(args[0]))
	
import xpybuild.utils.fileutils
from xpybuild.utils.flatten import flatten

isDirPath = xpybuild.utils.fileutils.isDirPath
"""Returns true if the path is a directory (ends with a slash, / or \\\\). """