from xpybuild.buildcommon import enableLegacyXpybuildModuleNames
enableLegacyXpybuildModuleNames()

import os, logging

from propertysupport import *
from buildcommon import *

import pathsets as oldpathsets
import xpybuild.pathsets as newpathsets

# deliberately use old names
from targets.writefile import WriteFile
from targets.copy import Copy

defineOutputDirProperty('OUTPUT_DIR', None)

writefiletarget = WriteFile('${OUTPUT_DIR}/foo.txt', 'Hello world')

# this is to check that the isinstance checks used by PathSet are working right - new and old package names should not have different pathsets! If they do, this will error. 
Copy('${OUTPUT_DIR}/copy1/', newpathsets.PathSet(oldpathsets.PathSet('${OUTPUT_DIR}/foo.txt')))
Copy('${OUTPUT_DIR}/copy2/', oldpathsets.PathSet(newpathsets.PathSet('${OUTPUT_DIR}/foo.txt')))

# check that names from before v3.0 still work and map to the new names
import xpybuild.buildcontext
assert xpybuild.buildcontext.getBuildInitializationContext() == xpybuild.buildcontext.BuildInitializationContext.getBuildInitializationContext()

assert normpath, "buildcommon.normpath should still be defined even though it's deprecated"

import propertyfunctors, xpybuild.propertysupport
assert propertyfunctors.joinPaths == xpybuild.propertysupport.joinPaths
assert xpybuild.propertysupport.joinPaths == joinPaths 

assert propertyfunctors.dirname == xpybuild.propertysupport.dirname
assert propertyfunctors.basename == xpybuild.propertysupport.basename
assert propertyfunctors.sub == xpybuild.propertysupport.sub
assert propertyfunctors.make_functor == xpybuild.propertysupport.make_functor

import buildexceptions
import xpybuild.utils.buildexceptions
assert buildexceptions.BuildException == xpybuild.utils.buildexceptions.BuildException

import targets.touch
assert targets.touch.Touch # aliased to writefile

import targets.unpack, targets.zip, targets.tar # all moved to archive
assert targets.unpack.Unpack
assert targets.zip.Zip
assert targets.tar.Tarball

import basetarget
assert basetarget.targetNameToUniqueId # alias for BaseTarget.targetNameToUniqueId
assert writefiletarget.addHashableImplicitInput # alias on target instances
assert writefiletarget.addHashableImplicitInputOption
