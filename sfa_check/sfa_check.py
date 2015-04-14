#!/usr/bin/env python

#   This file is part of sfa_check
#
#   Copyright 2015 Blake Caldwell
#   Oak Ridge National Laboratory
#
#   sfa_check is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   sfa_check is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this sfa_check.  If not, see <http://www.gnu.org/licenses/>.
#

"""
sfa_api_check.py

A script implmenting the DDN SFA API for checking the
health of SFA family storage controllers
"""

# modules used in this script
from ddn.sfa.api import *
from argparse import ArgumentParser, ArgumentError
import os
import sys
import multiprocessing 
import time
import socket
from traceback import print_exc
import re

# Keep track of the check modules that have been implemented.
# This is mainly for user input verification (or snnmp extend)
implementedModules = [ 'controller', 'internaldisk', 'virtualdisk', 'pool', 'disk', 'expander', 'ioc', 'channel', 'fan', 'power', 'sep', 'temperature', 'ups', 'voltage', 'icl_chan', 'icl_ioc', 'raid', 'host_chan' ]
defaultConfig = "/usr/local/etc/sfa_check.conf"

def enum(*sequential, **named):
    """ Helper function to define the enum structures used by DDN """
    enums = dict(zip(sequential, range(len(sequential))), **named)
    reverse = dict((value, key) for key, value in enums.iteritems())
    enums['reverse_mapping'] = reverse
    return type('Enum', (), enums)
def enum_nonseq(**enums):
    return type('Enum', (), enums)

statesWithUnknownValues=[]

# The enums gathered from the SFAOS 1.5.3.0 API documentation
NagiosStatus = enum('OK','WARNING','CRITICAL','UNKNOWN')

SFAMirrorState = enum('UNKNOWN','MEMBER','FAILED','STOPPED','COPYING','MISSING','NOTMIR','SYSDISK')
SFASESState = enum('UNSUPPORTED','OK','CRITICAL','NON_CRITICAL','UNRECOVERABLE','UNKNOWN','NOT_AVAIL','NO_ACCESS')
SFASESStatus = enum('UNSUPPORTED','OK','CRITICAL','NON_CRITICAL','UNRECOVERABLE','NOT_INSTALLED','UNKNOWN','NOT__AVAIL','NO_ACCESS')
SFAPoolCacheState = enum('ENABLED','DISABLED','ENABLING','DISABLING')
SFAMirrorState = enum('UNKNOWN','MEMBER','FAILED','STOPPED','COPYING','MISSING','NOTMIR','SYSDISK')

SFAHealthState = enum('NA','OK','NON_CRITICAL','CRITICAL')
statesWithUnknownValues.append(SFAHealthState)
SFAChannelType = enum('INVALID','FC','IB')
statesWithUnknownValues.append(SFAChannelType)
SFALinkState = enum('NONE','UP','DOWN')
statesWithUnknownValues.append(SFALinkState)
SFASESStatus = enum('UNSUPPORTED','OK','CRITICAL','NON_CRITICAL','UNRECOVERABLE','NOT_INSTALLED','UNKNOWN','NOT_AVAILABLE','NO_ACCESS')
SFAConState = enum('NA','STARTING','RUNNING','SHUTTING_DOWN','DOWN','OFFLINE')
statesWithUnknownValues.append(SFAConState)
SFAStoragePoolCacheState = enum('ENABLED','DISABLED','ENABLING','DISABLING')
SFAICCState = enum('INVALID','DOWN','DEGRADED','UP')
statesWithUnknownValues.append(SFAICCState)
SFAIOCType = enum('INVALID','FOREIGN','SAS_HBA','FC_HBA','IB_HCA','EN_NIC','PCIE_NTB')
statesWithUnknownValues.append(SFAIOCType)
SFAMIRReason = enum('NONE','JIS_DISCOVERY_IN_PROGRESS','OTHER_JIS_DISCOVERY_IN_PROGRESS','NO_BOBS','NO_CONFIG','NO_BACKEND','NO_JIS_MATCH','INCARNATION_MISMATCH','MULTIPLE_JIS','NO_QUORUM','NOT_LAST_CONTROLLER','DUAL_NO_AGREE','NO_JIS','CONFIG_MISMATCH','NO_LOAD_CONFIG','VD_CONFLICT','VERSION_MISMATCH','NO_READ_CONFIG','NO_MIRROR_JOIN','NEED_MIRRORED_DATA','MEMORY_MISMATCH','STALE_QUORUM','ALL_SMALL_SSDS','TOO_FW_QUORUM')
statesWithUnknownValues.append(SFAMIRReason)
SFAPoolState = enum('NORMAL','WAITING','FAILED','FAULT','DEGRADED','NORED','INOP','EXPORTED','EXPORTING')
statesWithUnknownValues.append(SFAPoolState)
SFAVDState = enum('NONE','NOT_READY','READY','INOPERATIVE','AUTO_WRITE_LOCKED','CRITICAL','DELETED','FORCED_WRITE_THROUGH','INITIALIZATION_FAILED')
statesWithUnknownValues.append(SFAVDState)
SFADiskHealthState = enum('GOOD','FAILED','FAILURE_PREDICTED')
statesWithUnknownValues.append(SFADiskHealthState)
SFADiskState = enum('READY','ABSENT','ASSUMED_PRESENT','PARTIAL_READY_LOCAL','PARTIAL_READY_REMOTE','STARTING','STOPPED')
statesWithUnknownValues.append(SFADiskState)
SFADiskMemberState = enum('NORMAL','MISSING','AMISS','RBLD','WRTB','FAILED','MNRB','ERROR_REC','UNASSIGNED')
SFAIBPortState = enum('NOP','DOWN','INIT','ARMED','ACTIVE','ACTIVE_DEFER')
statesWithUnknownValues.append(SFAIBPortState)

# add the mappings for UNKNOWN
for state in statesWithUnknownValues:
  state.reverse_mapping[255]='UNKNOWN'
  state.UNKNOWN=255

SFAWarningStatus = enum('NONE','TEMP_FAILURE','LOW_VOLTAGE','TEMP_WARNING','REPLACE_REQUIRED','LOW_CHARGE')
SFAWarningStatus.reverse_mapping[65535]='UNKNOWN'
SFAWarningStatus.UNKNOWN=65535

SYMBOL_ERROR_THRESHOLD=20

# These are the common poolSettings that we will want to verify against
defaultPoolSettings = { 'DirectProtect':1,
                        'ReACT':True,
                        'IORouting':True,
                        'CacheMirroring':0,
                        'ReadAheadCache':False,
                        'WriteBackCache':0,
                        'VerifyEnabled':True }


def lists_equal(list1,list2):
    if len(list1) != len(list2):
        return False
    for index,value in enumerate(list1):
        if list1[index] != list2[index]:
            return False

    return True

def getPoolSettings(SFAPool):
    """ Helper function to get the cache settings given a SFAPool object """
    poolSettings = { 'DirectProtect':SFAPool.DirectProtect,
                     'ReACT':SFAPool.ReACT,
                     'IORouting':SFAPool.IORouting,
                     'CacheMirroring':SFAPool.CacheMirroring,
                     'ReadAheadCache':SFAPool.ReadAheadCache,
                     'WriteBackCache':SFAPool.WriteBackCache,
                     'VerifyEnabled':SFAPool.VerifyEnabled }
    return poolSettings

def setPoolSettingsString(poolSettings):
    """ Create the settings string like shown on the CLI with show pool * """
    settingsString = ''
    if poolSettings['DirectProtect'] == 1:
        settingsString += 'D'
    elif poolSettings['DirectProtect'] == 2:
        settingsString += 'P'
    else:
        settingsString += ' '

    if poolSettings['WriteBackCache'] == 0:
        settingsString += 'W'
    else:
        settingsString += ' '

    if poolSettings['CacheMirroring'] == 0:
        settingsString += 'M'
    else:
        settingsString += ' '

    if poolSettings['ReadAheadCache']:
        settingsString += 'R'
    else:
        settingsString += ' '

    if poolSettings['ReACT']:
        settingsString += 'Re'
    else:
        settingsString += ' '

    if poolSettings['IORouting']:
        settingsString += 'I'
    else:
        settingsString += ' '

    if poolSettings['VerifyEnabled']:
        settingsString += 'V'
    else:
       settingsString += ' '

    return settingsString

def splitSubName(sub):
    controller_names = sub.split(',')
    if len(controller_names) > 0:
        return controller_names
    else:
        return None

class SFASystem (object):
    """ Class to hold information about the SFA Subsystem """
    def __init__(self, controller ,poolSettings):
        """ Constructor that includes the poolSettings """
        self.production = controller['production']
        self.host = controller['ip']
        self.auth = controller['auth']
        self.poolSettings = poolSettings
        self.settingsString = setPoolSettingsString(self.poolSettings)
        self.systemName = ''

    def __init__(self, controller):
        """ Constructor without the poolSettings. We will set them to arbitrarily defined default is """
        global defaultPoolSettings
        self.production = controller['production']
        self.host = controller['ip']
        self.auth = controller['auth']
        self.poolSettings = defaultPoolSettings
        self.settingsString = setPoolSettingsString(self.poolSettings)
        self.systemName = ''

class APIworker (object):
    def __init__(self, controller, modules, verbose, nagiosMode):
        self.verbose = verbose
        self.modules = modules
        self.controller = controller
        self.nagiosMode = nagiosMode
    def run(self):
        rc = NagiosStatus.UNKNOWN
        try:
            (ret_str, rc) = call_API(self.controller,self.modules,self.verbose,self.nagiosMode)
        except Exception, err:
            # there was another error while calling the checks for this subsystem 
            rc = NagiosStatus.UNKNOWN
            ret_str = "%s: %s"%(self.controller['sub_name'].split(",")[0],"UNKNOWN: Python Exception")
            if not self.nagiosMode:
                print "Exception %s: %s"%(err.__class__.__name__, err)
                print_exc()
        return (ret_str, rc)

class checkList (object):
    def __init__(self,thisSFA,modules,verbose,nagiosMode):
        self.checks = []
        for component in modules:
            if component == "controller":
                self.checks.append(controllerCheck(thisSFA,verbose,nagiosMode))
            if component == "channel":
                self.checks.append(channelCheck(thisSFA,verbose,nagiosMode))
            if component == "disk":
                self.checks.append(diskCheck(thisSFA,verbose,nagiosMode))
            if component == "expander":
                self.checks.append(expanderCheck(thisSFA,verbose,nagiosMode))
            if component == "fan":
                self.checks.append(fanCheck(thisSFA,verbose,nagiosMode))
            if component == "ioc":
                self.checks.append(iocCheck(thisSFA,verbose,nagiosMode))
            if component == "power":
                self.checks.append(powerCheck(thisSFA,verbose,nagiosMode))
            if component == "sep":
                self.checks.append(sepCheck(thisSFA,verbose,nagiosMode))
            if component == "pool":
                self.checks.append(poolCheck(thisSFA,verbose,nagiosMode))
            if component == "temperature":
                self.checks.append(temperatureCheck(thisSFA,verbose,nagiosMode))
            if component == "ups":
                self.checks.append(upsCheck(thisSFA,verbose,nagiosMode))
            if component == "virtualdisk":
                self.checks.append(virtualDiskCheck(thisSFA,verbose,nagiosMode))
            if component == "voltage":
                self.checks.append(voltageCheck(thisSFA,verbose,nagiosMode))
            if component == "internaldisk":
                self.checks.append(internalDiskCheck(thisSFA,verbose,nagiosMode))
            if component == "icl_ioc":
                self.checks.append(ICLIOCCheck(thisSFA,verbose,nagiosMode))
            if component == "icl_chan":
                self.checks.append(ICLChannelCheck(thisSFA,verbose,nagiosMode))
            if component == "raid":
                self.checks.append(RAIDProcessorCheck(thisSFA,verbose,nagiosMode))
            if component == "host_chan":
                self.checks.append(HostChannelCheck(thisSFA,verbose,nagiosMode))

def call_API(controller,modules,verbose,nagiosMode):
    rc = 1
    ret_str = []
    new_rc = 0
    returnStrings = []
    numChecksWARNING = 0
    numChecksUNKNOWN = 0
    numChecksCRITICAL = 0 
    if nagiosMode:
      devnull = open(os.devnull, 'w')
      sys.stderr = devnull

    thisSFA = SFASystem(controller)

    sfa = APIConnect("https://" + thisSFA.host, thisSFA.auth)
    thisSFA.systemName = SFAStorageSystem.get().Name

    # run the checks
    for check in checkList(thisSFA,modules,verbose,nagiosMode).checks:
        check_return_string = []
        try:
            check_results = check.doCheck()
            grammar = "Checks"
            # if check rc is higher than overall rc, change it
            if check_results['rc'] > new_rc:
                new_rc = check_results['rc']
         
            # update overall check counts
            numChecksWARNING += check_results['numChecksWARNING']
            numChecksUNKNOWN += check_results['numChecksUNKNOWN']
            numChecksCRITICAL += check_results['numChecksCRITICAL']

            # print check return value counts. If we are in Nagios mode, then only print out the count with
            # the highest severity

            if nagiosMode:
                extra = ''
                if check_results['message']:
                    # If we have extra message and are in nagiosMode then add it.
                    # if not in nagiosMode, this information is probably already part of output
                    extra = " - %s"%check_results['message']
                if check_results['numChecksCRITICAL'] > 0:
                    if check_results['numChecksCRITICAL'] == 1:
                        grammar = "Check"
                        check_return_string.append("CRITICAL%s"%extra)
                    else:
                        check_return_string.append("%s %s CRITICAL%s"%(check_results['numChecksCRITICAL'],grammar,extra))
                elif check_results['numChecksUNKNOWN'] > 0:
                    if check_results['numChecksUNKNOWN'] == 1:
                        grammar = "Check"
                        check_return_string.append("UNKNOWN%s"%extra)
                    else:
                        check_return_string.append("%s %s UNKNOWN%s"%(check_results['numChecksUNKNOWN'],grammar,extra))
                elif check_results['numChecksWARNING'] > 0:
                    if check_results['numChecksWARNING'] == 1:
                        grammar = "Check"
                        check_return_string.append("WARNING%s"%extra)
                    else:
                        check_return_string.append("%s %s WARNING%s"%(check_results['numChecksWARNING'],grammar,extra))
            else:
                if check_results['numChecksCRITICAL'] > 0:
                    if check_results['numChecksCRITICAL'] == 1:
                        grammar = "Check"
                    check_return_string.append("%s %s CRITICAL"%(check_results['numChecksCRITICAL'],grammar))
                if check_results['numChecksUNKNOWN'] > 0:
                    if check_results['numChecksUNKNOWN'] == 1:
                        grammar = "Check"
                    check_return_string.append("%s %s UNKNOWN"%(check_results['numChecksUNKNOWN'],grammar))
                if check_results['numChecksWARNING'] > 0:
                    if check_results['numChecksWARNING'] == 1:
                        grammar = "Check"
                    check_return_string.append("%s %s WARNING"%(check_results['numChecksWARNING'],grammar))

            # if there are warning, critical, or unknown checks, print them
            if check_results['rc'] > 0:
                returnStrings.append("%s: %s"%(check.description,'; '.join(check_return_string)))
            if len(check_results['ret_str']) > 0:
                for string in check_results['ret_str']:
                    returnStrings.append(string)
                if not nagiosMode:
                    returnStrings.insert(0,"Messages from check %s"%check.description)
                    returnStrings.append("\n")
        except Exception, err:
            numChecksUNKNOWN += 1
            returnStrings.append("%s: %s"%(check.description,"UNKNOWN: Python Exception"))
            if not nagiosMode:
                print "Exception %s: %s"%(err.__class__.__name__, err)
                print_exc()
 
    # if this is a non-production system, downgrade return status to WARNING
    if not thisSFA.production:
        if new_rc > 1:
            rc = 1

    # if its all OK then that's all we need to know
    if new_rc == NagiosStatus.OK:
        rc = NagiosStatus.OK
        returnStrings = []
        returnStrings.insert(0,"All Checks OK")

    # print check counts for all checks
    if nagiosMode:
        # for nagios output, concatenate ouput on a single line
        ret_str = ' ;; '.join(returnStrings)
        # if return status is not OK, then prepend NON-PROD 
        if new_rc != NagiosStatus.OK:
            if not thisSFA.production:
                ret_str = "NON-PROD - " + ret_str
    else:
        # if running from the CLI in extended mode, make it pretty
        returnStrings.insert(0,"-------------------------")
        returnStrings.insert(0,"\n%s Check Summary:"%thisSFA.systemName)
        ret_str = "\n".join(returnStrings)
        # if return status is not OK, then prepend NON-PROD
        if new_rc != NagiosStatus.OK and not thisSFA.production:
            ret_str = "%s is NON-PRODUCTION\n"%thisSFA.systemName + ret_str
    # clean up. disconnect execution context
    APIDisconnect()
    return ((ret_str,rc))

class APICheck(object):
    """ Abstract class for SFA checks """
    def __init__(self, thisSFA, description, verbose,nagiosMode):
        self.description = description
        self.verbose = verbose
        self.fault = NagiosStatus.OK
        self.thisSFA = thisSFA
        self.nagiosMode = nagiosMode
        self.numChecksWARNING = 0
        self.numChecksUNKNOWN = 0
        self.numChecksCRITICAL = 0 
        # this was added to support extra messages in the output (i.e. pool state)
        self.message = ''
        # this was added to capture output in non-nagios mode for printing later
        self.ret_str = []
    def printIfHealthy(self):
        """ Print a standard "healthy" message to stdout """
        if self.fault == NagiosStatus.OK:
            print "All {0} for {1} are healthy.".format(self.description,self.thisSFA.systemName)
    def setFaultWARNING(self):
        """ Set the fault return status to WARNING only if overriding OK """
        if self.fault == NagiosStatus.OK:
            self.fault = NagiosStatus.WARNING
        self.numChecksWARNING += 1
    def setFaultCRITICAL(self):
        """ Set the fault return status to CRITICAL unconditionally """
        self.fault = NagiosStatus.CRITICAL
        self.numChecksCRITICAL += 1
    def setFaultUNKNOWN(self):
        """ Set the fault return status to UNKNOWN if not overriding CRITICAL """
        if not self.fault == NagiosStatus.CRITICAL:
            self.fault = NagiosStatus.UNKNOWN
        self.numChecksUNKNOWN += 1
    def createCheckReturnValues(self):
        returnDict = {}
        returnDict['rc'] = self.fault
        returnDict['numChecksWARNING'] = self.numChecksWARNING
        returnDict['numChecksUNKNOWN'] = self.numChecksUNKNOWN
        returnDict['numChecksCRITICAL'] = self.numChecksCRITICAL
        returnDict['message'] = self.message
        returnDict['ret_str'] = self.ret_str
        return returnDict

    def doHealthCheck(self,SFAClass,objectStatePropertyStr,objectStateEnum,objectStateValueStr,extraIdentifiers,ignoreChildHealth):
        """ This method is the workhorse for the script. Since we can't capture every unique property to check
            for the different SFA components, just capture the checks of HealthState, ChildHealthState, and one
            other to display along with HealthState when not OK.
            
            ******* Remember when updating this function also update channelCheck.doHealthCheck() ***********
        """
        for object in SFAClass.getAll():
            messages = []
            # Capture the HealthState even if its OK. If there is another fault with ChildHealthState or
            # object.objectStatePropertyStr, we will still want to print it out
            healthStateMessage = "Health: {0}".format(SFAHealthState.reverse_mapping[object.HealthState])
            if object.HealthState != SFAHealthState.OK:
                if object.HealthState == SFAHealthState.CRITICAL:
                    # this object has a critical state, if not in nagiosMode, print this on the console
                    if objectStatePropertyStr and objectStateEnum:
                        if self.nagiosMode == False:
                            messages.append("{0}: {1}".format(objectStatePropertyStr,objectStateEnum.reverse_mapping[getattr(object,objectStatePropertyStr)]))
                    self.setFaultCRITICAL()
                elif object.HealthState == SFAHealthState.NON_CRITICAL:
                    self.setFaultWARNING()
                else:
                    self.setFaultUNKNOWN()
            elif objectStatePropertyStr and objectStateEnum and objectStateValueStr and getattr(object,objectStatePropertyStr) != getattr(objectStateEnum,objectStateValueStr):
                messages.append("{0}: {1}".format(objectStatePropertyStr,objectStateEnum.reverse_mapping[getattr(object,objectStatePropertyStr)]))
                self.setFaultWARNING()
            if (self.nagiosMode == False) and (ignoreChildHealth == False):
                if object.ChildHealthState != SFAHealthState.OK:
                    messages.append("Child Health: {0}".format(SFAHealthState.reverse_mapping[object.ChildHealthState]))
                    self.setFaultWARNING()
            # Anything to print out?
            if messages or object.HealthState != SFAHealthState.OK:
                # anything to print after HealthState?
                if messages:
                    separator = ';'
                else:
                    separator = ''
                extraIdentStr = ''
                if extraIdentifiers:
                    for extraIdent in extraIdentifiers:
                        extraIdentStr += " {0}: {1}".format(extraIdent,getattr(object,extraIdent))
                if (self.nagiosMode == False):
                    self.ret_str.append("{0} {1} {2} Index: {3}{4} {5}".format(self.description,healthStateMessage,extraIdentStr,object.Index,separator,'; '.join(messages)))
        if self.verbose == True:
            self.printIfHealthy()
        return self.fault

class controllerCheck(APICheck):
    """ Class for checking the controller units """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'CONTROLLER',verbose,nagiosMode)
    def doCheck(self):
        """ Function implementing the controller check
     
            calls APICheck.doHealthCheck() to check HealthState, ChildHealthState, and State
            
            Need to special case SFA10k doHealthCheck because we want to ignore ChildHealthState
             
            Also checks:
              1. RestartPending == False
              2. MIR state == NON
        """
        SFAType = SFAController.getAll()[0].VendorEquipmentType.rstrip()
        #if "SFA 10000" in SFAType or self.nagiosMode:
        #    ignoreChildHealth = True
        #else:
        ignoreChildHealth = False

        extraCheckProperty = 'State'
        extraCheckPropertyDesiredValue = 'RUNNING'
        extraCheckPropertyValues = SFAConState
        extraIdentifiers = ['Name']
        self.fault = self.doHealthCheck(SFAController,extraCheckProperty,extraCheckPropertyValues,extraCheckPropertyDesiredValue,extraIdentifiers,ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAController.getAll():
            messages = []
            if object.RestartPending:
                messages.append("Restart Pending")
                self.setFaultWARNING()
            if object.MIRReason != SFAMIRReason.NONE:
                messages.append("MIR Reason {0}".format(SFAMIRReason.reverse_mapping[object.MIRReason]))
                self.setFaultCRITICAL()
            if object.ICCState != SFAICCState.UP:
                messages.append("ICC State:{0} ICCProtocolVersion:{1}".format(SFAICCState.reverse_mapping[object.ICCState],object.ICCProtocolVersion))
                self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} Index: {1}; {2}".format(self.description,object.Index,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class channelCheck(APICheck):
    """ Check the disk channels
        These are redundant components, so should return WARNING
 
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'DISK CHANNEL',verbose,nagiosMode)
    def checkDiskChannelSpeed(self,SFAType,channelObject):
        """ Check that the CurrentSpeed is at the highest value.
            The SFA10K may have 3Gbs SAS channels, which is OK
        """
        if "SFA 10000" in SFAType:
            if channelObject.CurrentSpeed >= 3:
                return NagiosStatus.OK
        elif channelObject.CurrentSpeed >= 6:
            return NagiosStatus.OK
        else:
            return NagiosStatus.WARNING

    def skipDiskChannel(self,SFAType,channelObject):
        """
            For some configurations (SFA12K-40), some channels will
            show up as DOWN, but they are just not wired in that
            configuration, so those channels are just ignored
        """

        ignoreLocations = { 'SFA12K-40':["3-3","6-3","4-1","7-1"],
                            'SFA12KXN':["3-3","6-3","4-1","7-1"],
                            'SFA 10000':["P0.3-B","P0.3-D","P0.4-B","P0.4-D","P1.2-B","P1.2-C","P1.3-A","P1.3-C","P1.4-A","P1.4-C"] }

        if channelObject.PortLocation in ignoreLocations[SFAType]:
            return True
        else:
            return False
    def doCheck(self):
        """ Function implementing the channel check 
            Perform custom health checks for SFA10K and SFA12K controllers using channelCheck.doHealthCheck()

            Also Check:
              1 CurrentSpeed = 6 Gbs (3 for 10k)
              2. CurrentWidth == ExpectedWidth (4)
              3. CurrentPosition == ExpectedPosition (useful or not?)
        """
        SFAType = SFAController.getAll()[0].VendorEquipmentType.rstrip()
        
        # do our own health check
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False

        extraCheckProperty = 'LinkState'
        extraCheckPropertyDesiredValue = 'UP'
        extraCheckPropertyValues = SFALinkState
        extraIdentifiers = ['ControllerIndex','PortLocation']
        # do our own health check
        self.fault = self.doHealthCheck(SFAType,SFADiskChannel,extraCheckProperty,extraCheckPropertyValues,extraCheckPropertyDesiredValue,extraIdentifiers,ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        # Do the other checks
        for object in SFADiskChannel.getAll():
            messages = []
            if self.skipDiskChannel(SFAType,object):
                continue
            # only run these checks if channel is UP, otherwise Speed and Width tell us nothing
            if object.LinkState == SFALinkState.UP:
                if self.checkDiskChannelSpeed(SFAType,object) != NagiosStatus.OK:
                    messages.append("Speed:{0} AvailableSpeeds:{1}".format(object.CurrentSpeed,','.join([str(speed) for speed in object.AvailableSpeeds])))
                    self.setFaultWARNING()
                if object.CurrentWidth != object.ExpectedWidth:
                    messages.append("Width:{0} ExpectedWidth:{1}".format(object.CurrentWidth,object.ExpectedWidth))
                    self.setFaultWARNING()
                # based on the IOCPort we can determine the expected PHYs for an UP port
                expectedPHYs = []
                if (object.IOCPort == 4):
                  expectedPHYs = [4,5,6,7]
                elif (object.IOCPort == 0):
                  expectedPHYs = [0,1,2,3]
                else:
                    messages.append("PHYs: {0} Unexpected IOC port {1}".format(object.PHYs,object.IOCPort))
                    self.setFaultWARNING()
                if len(expectedPHYs) > 0:
                    if not lists_equal(object.PHYs,expectedPHYs):
                        messages.append("PHYs: {0} Expected PHYs for IOC port {1}: {2}".format(object.PHYs,object.IOCPort,expectedPHYs))
                        self.setFaultWARNING()
            if object.CurrentPosition != object.ExpectedPosition:
               messages.append("Position: {0} ExpectedPosition: {1}".format(','.join([str(position) for position in object.CurrentPosition]),','.join([str(position) for position in object.ExpectedPosition])))
               # it's not clear this is an actual problem, so only set to warning when not run in nagios mode.
               if self.nagiosMode == False:
                   self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} Controller: {1} Port {2}; {3}".format(self.description,object.ControllerIndex, object.PortLocation,'; '.join(messages)))
        return self.fault


    def doHealthCheck(self,SFAType,SFAClass,objectStatePropertyStr,objectStateEnum,objectStateValueStr,extraIdentifiers,ignoreChildHealth):
        """ We need to use a slight modification of APICheck.doHealthCheck() that skips certain
            disk channels for SFA10K and SFA12K systems. This is handled by the channelCheck.skipDiskChannel()
            method and has specific locations for both hardware platforms. It would be ideal to get rid of
            this method since it has specific knowledge inside it. Hopefully this is fixed in a future
            SFAOS release.
             
            Other than checking if the channel should be skipped, this function is identical to APICheck.doHealthCheck()
        """
        for object in SFAClass.getAll():
            if self.skipDiskChannel(SFAType,object):
                continue
            messages = []
            # Capture the HealthState even if its OK. If there is another fault with ChildHealthState or
            # object.objectStatePropertyStr, we will still want to print it out
            healthStateMessage = "Health: {0}".format(SFAHealthState.reverse_mapping[object.HealthState])
            if object.HealthState != SFAHealthState.OK:
                if object.HealthState == SFAHealthState.CRITICAL:
                    if objectStatePropertyStr and objectStateEnum:
                        messages.append("{0}: {1}".format(objectStatePropertyStr,objectStateEnum.reverse_mapping[getattr(object,objectStatePropertyStr)]))
                    self.setFaultCRITICAL()
                elif object.HealthState == SFAHealthState.NON_CRITICAL:
                    self.setFaultWARNING()
                else:
                    self.setFaultUNKNOWN()
            elif objectStatePropertyStr and objectStateEnum and objectStateValueStr and getattr(object,objectStatePropertyStr) != getattr(objectStateEnum,objectStateValueStr):
                messages.append("{0}: {1}".format(objectStatePropertyStr,objectStateEnum.reverse_mapping[getattr(object,objectStatePropertyStr)]))
                self.setFaultWARNING()
            if (self.nagiosMode == False) and (ignoreChildHealth == False):
                if object.ChildHealthState != SFAHealthState.OK:
                    messages.append("Child Health: {0}".format(SFAHealthState.reverse_mapping[object.ChildHealthState]))
                    self.setFaultWARNING()

            # Anything to print out?
            if messages or object.HealthState != SFAHealthState.OK:
                # anything to print after HealthState?
                if messages:
                    separator = ';'
                else:
                    separator = ''
                extraIdentStr = ''
                if extraIdentifiers:
                    for extraIdent in extraIdentifiers:
                        extraIdentStr += " {0}: {1}".format(extraIdent,getattr(object,extraIdent))
                if (self.nagiosMode == False):
                    self.ret_str.append("{0} {1} {2} Index: {3}{4} {5}".format(self.description,healthStateMessage,extraIdentStr,object.Index,separator,'; '.join(messages)))
                #else:
                #    self.ret_str.append("{0}{1} Index: {2}; {3}{4} {5}".format(self.description,extraIdentStr,object.Index,healthStateMessage,separator,'; '.join(messages)))

        if self.verbose == True:
            self.printIfHealthy()
        returnValues = self.createCheckReturnValues()
        return returnValues

class HostChannelCheck(APICheck):
    """ Check the HOST Channels on the SFA
        Check only (no Health):
          1. LinkState
          2. Speed
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'HOST CHANNEL',verbose,nagiosMode)
    def doCheck(self):
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAHostChannel.getAll():
            messages = []
            if object.LinkState == SFALinkState.UP:
                if object.Speed != object.AvailableSpeeds:
                    messages.append("Host Channel Index:{0} has speed:{1} cabable of {2}".format(object.Index,object.Speed,object.AvailableSpeeds))
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    self.ret_str.append("{0} Host Channel Index:{1} Controller:{2}; {3}".format(self.description,object.Index,object.ControllerIndex,'; '.join(messages)))
                    roomLeftInNagiosOutput -= 1
        returnValues = self.createCheckReturnValues()
        return returnValues

class RAIDProcessorCheck(APICheck):
    """ Check the RAID Processors on the SFA
        Do the basic health checks
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'RAID PROCS',verbose,nagiosMode)
    def doCheck(self):  
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFARAIDProcessor,None,None,None,['ControllerIndex','IndexOnController'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFARAIDProcessor.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                # do nothing yet
                pass
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} ControllerIndex:{1} IndexOnController:{2}; {3}".format(self.description,object.ControllerIndex,object.IndexOnController,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class ICLIOCCheck(APICheck):
    """ Check the ICL links IO controller on the SFA
        Do the basic health
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'ICL IOC',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFAICLIOC,None,None,None,['ControllerIndex'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAICLIOC.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                # do nothing yet
                pass
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} ControllerIndex:{1}".format(self.description,object.ControllerIndex,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class ICLChannelCheck(APICheck):
    """ Check the ICL Channels on the SFA
        Also check:
          1. CurrentSpeed 
          if ICLIOCType == Infiniband
            2. InfinibandPortState == ACTIVE
            3. InfinibandCurrentWidth == 4
            4. ErrorStatisticCounts for SymbolErrors is below a threshold
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'ICL CHANNEL',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        extraCheckProperty = 'LinkState'
        extraCheckPropertyDesiredValue = None
        extraCheckPropertyValues = SFALinkState
        extraIdentifiers = ['ControllerIndex']
        self.fault = self.doHealthCheck(SFAICLChannel,extraCheckProperty,extraCheckPropertyValues,extraCheckPropertyDesiredValue,extraIdentifiers,ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAICLChannel.getAll():
            messages = []
            if object.LinkState == SFALinkState.UP:
                if object.CurrentSpeed != 10000:
                    messages.append("ICL Index:{0} has speed:{1}".format(object.Index,object.CurrentSpeed))
                    self.setFaultWARNING()
                if object.ICLIOCType == SFAIOCType.IB_HCA:
                    if object.InfinibandCurrentWidth != 4:
                        messages.append("ICL Index:{0} has width:{1}".format(object.Index,object.CurrentSpeed))
                        self.setFaultWARNING()
                    if object.InfinibandPortState != SFAIBPortState.ACTIVE:
                        messages.append("ICL Index:{0} is state:{1}".format(object.Index,SFAIBPortState.reverse_mapping[object.InfinibandPortState]))
                        self.setFaultWARNING()
                    if (object.ErrorStatisticNames[0] == "SymbolErrorCounter") and (object.ErrorStatisticCounts[0] > SYMBOL_ERROR_THRESHOLD):
                        messages.append("ICL Index:{0} SymbolErrorCounter:{1}".format(object.Index,object.ErrorStatisticCounts[0]))
                        self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    self.ret_str.append("{0} ICL Index:{1} Controller:{2}; {3}".format(self.description,object.Index,object.ControllerIndex,'; '.join(messages)))
                    roomLeftInNagiosOutput -= 1
        returnValues = self.createCheckReturnValues()
        return returnValues

class poolCheck(APICheck):
    """ Check the storage pools """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'POOL',verbose,nagiosMode)
    def doCheck(self):
        """ Function implementing the storage pool check
       
            Make sure there are no degraded or critical pools.

            Check:
              1. HealthState
              2. ChildHealthState
              3. PoolState == NORMAL
              4. Pool settings (cache,verify,DirectProtect) if nonproduction
        """
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        extraCheckProperty = 'PoolState'
        extraCheckPropertyDesiredValue = 'NORMAL'
        extraCheckPropertyValues = SFAPoolState
        extraIdentifiers = None
        extraMessage = ''
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        self.fault = self.doHealthCheck(SFAStoragePool,extraCheckProperty,extraCheckPropertyValues,extraCheckPropertyDesiredValue,extraIdentifiers,ignoreChildHealth)
        for pool in SFAStoragePool.getAll():
            messages = []
            if pool.PoolState == SFAPoolState.NORED:
                extraMessage = "Index %s NORED"%pool.Index
            if pool.PoolState == SFAPoolState.DEGRADED:
                extraMessage = "Index %s DEGRADED"%pool.Index
            if pool.HomeControllerIndex != pool.PreferredHomeControllerIndex or pool.HomeControllerRPIndex != pool.PreferredHomeControllerRPIndex:
                messages.append("Non-preferred home")
                extraMessage = "Index %s non-preferred home"%pool.Index
                self.setFaultWARNING()
            if pool.AutoWriteLocked:
                messages.append("Auto-write locked")
                extraMessage = "Index %s auto-write locked"%pool.Index
                self.fault = NagiosStatus.CRITICAL
            if pool.Rebuilding:
                messages.append("Rebuilding")
                if pool.PoolState != SFAPoolState.NORMAL:
                    extraMessage += " (REBUILDING)"
                self.setFaultWARNING()
            if pool.BadBlockCount > 0:
                messages.append("BadBlocks: {0}".format(pool.BadBlockCount))
                # Increasing bad blocks is normal as drive sectors get remapped. Don't trigger a warning
                # since the DDN will take automatic action based on this counter, but print it for debugging
                # when this script is run from the CLI in "extended" (-x) mode
                if self.nagiosMode == False:
                    self.setFaultWARNING()
            # These are probably just useful for development and testing environments, turn off for production
            if self.thisSFA.production == False:
                SFAPoolSettings = getPoolSettings(pool)
                if SFAPoolSettings != self.thisSFA.poolSettings:
                    SFASettingsString = setPoolSettingsString(SFAPoolSettings)
                    messages.append("Settings: {0} Expected: {1}".format(SFASettingsString,self.thisSFA.settingsString))
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    self.ret_str.append("Pool Index: {0}; {1}".format(pool.Index,'; '.join(messages)))
                    roomLeftInNagiosOutput -= 1
        if (extraMessage and self.fault != 0):
            self.message = extraMessage
        returnValues = self.createCheckReturnValues()
        return returnValues

class virtualDiskCheck(APICheck):
    """ Check the virtual disks on the SFA
        Do the basic health check plus:
          1. Check BadBlockCount
    """

    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'VIRTUAL DISK',verbose,nagiosMode)
    def doCheck(self):
        # ignoring ChildHealth, beacuse we'll get a message from the pool anyway
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
	    ignoreChildHealth = False
        extraCheckProperty = 'State'
        extraCheckPropertyDesiredValue = 'READY'
        extraCheckPropertyValues = SFAVDState
        extraIdentifiers = None
        self.fault = self.doHealthCheck(SFAVirtualDisk,extraCheckProperty,extraCheckPropertyValues,extraCheckPropertyDesiredValue,extraIdentifiers,ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAVirtualDisk.getAll():
            messages = []
	    if object.BadBlockCount > 0:
                messages.append("BadBlocks: {0}".format(object.BadBlockCount))
                # Increasing bad blocks is normal as drive sectors get remapped. Don't trigger a warning
                # since the DDN will take automatic action based on this counter, but print it for debugging
                # when this script is run from the CLI in "extended" (-x) mode
                if self.nagiosMode == False:
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    self.ret_str.append("{0} Index: {1}; {2}".format(self.description,object.Index,'; '.join(messages)))
                    roomLeftInNagiosOutput -= 1
        returnValues = self.createCheckReturnValues()
        return returnValues

class internalDiskCheck(APICheck):
    """ Check the internal disks on the SFA

        Print MirrorState along with Fault.
        Print enclosure index to indentify which controller
        Ignore that DISK C is missing on the SFA10K.
        
        TODO: need to Handle NOTMIR and Ses status NON-CRITICAL on tick-sfa10k1
 
        Also check:
          1. Present
          2. PredictFailure
          3. SESStatus
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'INTERNAL DISK',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        extraCheckProperty = 'MirrorState'
        extraCheckPropertyDesiredValue = None
        extraCheckPropertyValues = SFAMirrorState
        extraIdentifiers = ['EnclosureIndex','Name']
        self.fault = self.doHealthCheck(SFAInternalDiskDrive,extraCheckProperty,extraCheckPropertyValues,extraCheckPropertyDesiredValue,extraIdentifiers,ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAInternalDiskDrive.getAll():
            messages = []
            # Deal with the special case where the internal disks are in a NOTMIR state. We don't rely on the generic
            # doHealthCheck call above because is has no way of checking that SFAHealthState is NON_CRITICAL and 
            # SFAMirrorState is MEMBER. SFAMirrorState may show NOTMIR as a normal case when SFAHealthState is OK.
            # That is the case for the C disk in SFA10ks (see exception below for that case that we don't want to
            # know about)
            if object.HealthState == SFAHealthState.NON_CRITICAL and object.MirrorState != SFAMirrorState.MEMBER:
                messages.append("NOT MIRRORED")
            if object.HealthState == SFAHealthState.OK:
                if "SFA 10000" in SFAController.getAll()[0].VendorEquipmentType.rstrip():
                    if object.Name == 'DISK C':
                        continue
                if not object.Present:
                    messages.append("NOT PRESENT")
                    self.setFaultWARNING()
                if object.PredictFailure:
                    messages.append("PREDICTED FAILURE")
                    self.setFaultWARNING()
                if object.SESStatus != SFASESStatus.OK:
                    messages.append("SES Status: {0}".format(SFASESStatus.reverse_mapping[object.SESStatus]))
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    self.ret_str.append("{0} EnclosureIndex: {1} Name: {2}; {3}".format(self.description,object.EnclosureIndex,object.Name,'; '.join(messages)))
                    roomLeftInNagiosOutput -= 1
        returnValues = self.createCheckReturnValues()
        return returnValues

class diskCheck(APICheck):
    """ Check the disk drives 

        Check that State is READY (separate from MemberState)
        Print Index, SerialNumber, EnclosureIndex, DiskSlotNumber to help identify failed drive
        Also check that MemberState is NORMAL or UNASSIGNED (relative to pool)
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'DISK DRIVE',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        extraCheckProperty = 'State'
        extraCheckPropertyDesiredValue = 'READY'
        extraCheckPropertyValues = SFADiskState
        extraIdentifiers = ['EnclosureIndex','DiskSlotNumber','SerialNumber']
        extraMessage = ''
        self.fault = self.doHealthCheck(SFADiskDrive,'State',SFADiskState,'READY',['EnclosureIndex','DiskSlotNumber','SerialNumber'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for disk in SFADiskDrive.getAll():
            messages = []
            if disk.MemberState != SFADiskMemberState.NORMAL and disk.MemberState != SFADiskMemberState.UNASSIGNED:
                messages.append("MemberState: {0}".format(SFADiskMemberState.reverse_mapping[disk.MemberState]))
                extraMessage = "Index %s MemberState %s"%(disk.Index,SFADiskMemberState.reverse_mapping[disk.MemberState])
                # if its already critical, don't need to make this a warning
                if disk.HealthState == SFAHealthState.OK:
                    self.setFaultWARNING()
            if disk.DiskHealthState != SFADiskHealthState.GOOD:
                if disk.State == SFADiskState.UNKNOWN and disk.DiskHealthState == SFADiskHealthState.UNKNOWN:
                    # Disk state is unknown, so we are going to get UNKNOWN for DiskHealthState as well. don't repeat the message
                    pass
                else:
                    messages.append("DiskHealthState: {0}".format(SFADiskHealthState.reverse_mapping[disk.DiskHealthState]))
                    extraMessage = "Index %s State %s"%(disk.Index,SFADiskHealthState.reverse_mapping[disk.DiskHealthState])
                # if its already critical, don't need to make this a warning
                if disk.HealthState == SFAHealthState.OK:
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    self.ret_str.append("{0} Enclosure: {1} Slot: {2} Index: {3} SerialNumber: {4}; {5}".format(self.description,disk.EnclosureIndex, disk.DiskSlotNumber,disk.Index,disk.SerialNumber,'; '.join(messages)))
                    roomLeftInNagiosOutput -= 1
        if (extraMessage and self.fault != 0):
            self.message = extraMessage
        returnValues = self.createCheckReturnValues()
        return returnValues


class expanderCheck(APICheck):
    """ Check the SFA expanders
        
        Just check the HealthState as part of doCheck
        Also check:
          1. Present
          2. Not predicted failure
          3. SES Status == OK
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'EXPANDER',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFAExpander,None,None,None,['EnclosureIndex','Position','Location'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAExpander.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                if not object.Present:
                    messages.append("NOT PRESENT")
                    self.setFaultWARNING()
                if object.PredictFailure:
                    messages.append("PREDICTED FAILURE")
                    self.setFaultWARNING()
                if object.SESStatus != SFASESStatus.OK:
                    messages.append("SES Status: {0}".format(SFASESStatus.reverse_mapping[object.SESStatus]))
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} EnclosureIndex: {1} Position: {2} Location: {3} Index: {4}; {5}".format(self.description,object.EnclosureIndex,object.Position,object.Location,object.Index,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class fanCheck(APICheck):
    """ Check all fan units in the SFA 
        
        Just check the HealthState as part of doCheck
        Also check:
          1. Present
          2. Powered on
          3. SES Status == OK
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'FAN',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFAFan,None,None,None,['EnclosureIndex','Position','Location'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAFan.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                if not object.Present:
                    messages.append("NOT PRESENT")
                    self.setFaultWARNING()
                if not object.PoweredOn:
                    messages.append("NOT POWERED ON")
                    self.setFaultWARNING()
                if object.SESStatus != SFASESStatus.OK:
                    messages.append("SES Status: {0}".format(SFASESStatus.reverse_mapping[object.SESStatus]))
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} EnclosureIndex: {1} Position: {2} Location: {3} Index: {4}; {5}".format(self.description,object.EnclosureIndex,object.Position,object.Location,object.Index,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues


class iocCheck(APICheck):
    """ Check the SFA IOC modules """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'IOC', verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFAIOC,None,None,None,['ControllerIndex','RPIndexOnController','Slot'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAIOC.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                if object.ChannelCount != 2:
                    messages.append("ChannelCount: {0}".format(object.ChannelCount))
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} ControllerIndex: {1} RP: {2} Slot: {3} Index: {4}; {5}".format(self.description,object.ControllerIndex,object.RPIndexOnController,object.Slot,object.Index,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class powerCheck(APICheck):
    """ Check the SFA power supplies """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'POWER SUPPLY', verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFAPowerSupply,None,None,None,['EnclosureIndex','Location'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAPowerSupply.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                if not object.Present:
                    messages.append("NOT PRESENT")
                    self.setFaultWARNING()
                if not object.PowerState:
                    messages.append("NOT POWERED ON")
                    self.setFaultWARNING()
                if object.ACFailure:
                    messages.append("AC POWER FAILURE")
                    self.setFaultWARNING()
                if object.DCFailure:
                    messages.append("DC POWER FAILURE")
                    self.setFaultWARNING()
                if object.TemperatureFailure:
                    messages.append("TEMPERATURE CRITICAL")
                    self.setFaultWARNING()
                if object.TemperatureWarning:
                    messages.append("TEMPERATURE WARNING")
                    self.setFaultWARNING()
                if object.PredictFailure:
                    messages.append("FAILURE PREDICTED")
                    self.setFaultWARNING()
                if object.SESStatus != SFASESStatus.OK:
                    messages.append("SES Status: {0}".format(SFASESStatus.reverse_mapping[object.SESStatus]))
                    self.setFaultWARNING()
            if messages and self.fault != 0: 
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} EnclosureIndex: {1} Position: {2} Location: {3}; {4}".format(self.description,object.EnclosureIndex,object.Position,object.Location,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class upsCheck(APICheck):
    """ Check the UPS units """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'UPS',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFAUPS,'WarningStatus',SFAWarningStatus,'NONE',['EnclosureIndex'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAUPS.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                if not object.Present:
                    messages.append("NOT PRESENT")
                    self.setFaultWARNING()
                if not object.Enabled:
                    messages.append("NOT ENABLED")
                    self.setFaultWARNING()
                if object.ACFailure:
                    messages.append("AC FAILURE")
                    self.setFaultWARNING()
                if object.UPSFailure:
                    messages.append("UPS FAILURE")
                    self.setFaultWARNING()
                if object.InterfaceFailure:
                    messages.append("INTERFACE FAILURE")
                    self.setFaultWARNING()
                if object.PredictFailure:
                    messages.append("FAILURE PREDICTED")
                    self.setFaultWARNING()
                if object.SESStatus != SFASESStatus.OK:
                    messages.append("SES Status: {0}".format(SFASESStatus.reverse_mapping[object.SESStatus]))
                    self.setFaultWARNING()
            if messages and self.fault != 0: 
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} EnclosureIndex: {1}; {2}".format(self.description,object.EnclosureIndex,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class sepCheck(APICheck):
    """
    Check the SFA Enclosure Services Controller Electronics
    This includes indicator LEDs for Failure and Locate   
    """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'SEP',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFASEP,None,None,None,['EnclosureIndex','Position','Location'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAExpander.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                if not object.Present:
                    messages.append("NOT PRESENT")
                    self.setFaultWARNING()
                if object.PredictFailure:
                    messages.append("PREDICTED FAILURE")
                    self.setFaultWARNING()
                if object.SESStatus != SFASESStatus.OK:
                    messages.append("SES Status: {0}".format(SFASESStatus.reverse_mapping[object.SESStatus]))
                    self.setFaultWARNING()
            if messages and self.fault != 0: 
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} EnclosureIndex: {1} Position: {2} Location: {3} Index: {4}; {5}".format(self.description,object.EnclosureIndex,object.Position,object.Location,object.Index,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class temperatureCheck(APICheck):
    """ Check the SFA temperature sensors """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'TEMPERATURE',verbose,nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFATemperatureSensor,None,None,None,['EnclosureIndex','Position','Location'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFATemperatureSensor.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                if not object.Present:
                    messages.append("NOT PRESENT")
                    self.setFaultWARNING()
                if object.PredictFailure:
                    messages.append("PREDICTED FAILURE")
                    self.setFaultWARNING()
                if object.TemperatureFailure:
                    messages.append("TEMPERATURE FAILURE")
                    self.setFaultWARNING()
                if object.TemperatureWarning:
                    messages.append("TEMPERATURE WARNING")
                    self.setFaultWARNING()
                if object.SESStatus != SFASESStatus.OK:
                    messages.append("SES Status: {0}".format(SFASESStatus.reverse_mapping[object.SESStatus]))
            if messages and self.fault != 0: 
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.setFaultWARNING()
                    self.ret_str.append("{0} EnclosureIndex: {1} Position: {2} Location: {3} Index: {4}; {5}".format(self.description,object.EnclosureIndex,object.Position,object.Location,object.Index,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

class voltageCheck(APICheck):
    """ Check the SFA Voltage Sensors """
    def __init__(self, thisSFA, verbose, nagiosMode):
        APICheck.__init__(self,thisSFA,'VOLTAGE',verbose, nagiosMode)
    def doCheck(self):
        if self.nagiosMode:
            ignoreChildHealth = True
        else:
            ignoreChildHealth = False
        self.fault = self.doHealthCheck(SFAVoltageSensor,None,None,None,['EnclosureIndex','Position','Location'],ignoreChildHealth)
        # allow one messages line in nagios output
        roomLeftInNagiosOutput = 2
        for object in SFAVoltageSensor.getAll():
            messages = []
            if object.HealthState == SFAHealthState.OK:
                if not object.Present:
                    messages.append("NOT PRESENT")
                    self.setFaultWARNING()
                if object.PredictFailure:
                    messages.append("PREDICTED FAILURE")
                    self.setFaultWARNING()
                if object.OverVoltageFailure:
                    messages.append("OVER VOLTAGE FAILURE")
                    self.setFaultWARNING()
                if object.OverVoltageWarning:
                    messages.append("OVER VOLTAGE WARNING")
                    self.setFaultWARNING()
                if object.UnderVoltageFailure:
                    messages.append("UNDER VOLTAGE FAILURE")
                    self.setFaultWARNING()
                if object.UnderVoltageWarning:
                    messages.append("UNDER VOLTAGE WARNING")
                    self.setFaultWARNING()
                if object.SESStatus != SFASESStatus.OK:
                    messages.append("SES Status: {0}".format(SFASESStatus.reverse_mapping[object.SESStatus]))
                    self.setFaultWARNING()
            if messages and self.fault != 0:
                if (self.nagiosMode == False or roomLeftInNagiosOutput > 0):
                    roomLeftInNagiosOutput -= 1
                    self.ret_str.append("{0} EnclosureIndex: {1} Position: {2} Location: {3} Index: {4} {5}".format(self.description,object.EnclosureIndex,object.Position,object.Location,object.Index,'; '.join(messages)))
        returnValues = self.createCheckReturnValues()
        return returnValues

def readConfig(config_file):
  config = []

  if not os.path.exists(config_file):
      sys.stderr.write("Configuration file %s could not be found\n"%config_file)
      return None

  f = open(config_file,'r')
  lines = f.readlines()

  for line in lines:
    # remove comments and split so accept lines with comments at end
    if "#" in line:
        if re.match("^[ \t]*#",line):
          continue
        else:
          line = line.split('#')[0]
    if re.match("^[ \t]*$",line):
        # blank line
        continue
    config_list = line.split(':')
    sub_oid = config_list[0].strip()
    controllers =  config_list[1].strip()
    prod_int = int(config_list[2].strip())
    if prod_int == 1:
        production = True
    else:
        production = False

    auth = ("user","user")
    if len(config_list) > 3:
        if "," in config_list[3]:
            auth = config_list[3].strip().split(',')
    
    if re.search("[ \t]",controllers) or re.search(",{2,}",controllers):
        syslog.syslog(syslog.LOG_WARNING,"Bad controller pair %s"%controllers)
        continue

    # add a tuple to the config list
    config.append((sub_oid, controllers, production, auth))
  return config


def run(foo):
    """ Very dumb function that is just used to pickle an APIworker method for the 
        pool.apply_async() call. Returns APIworker.run() """
    return foo.run()



def sfaAPICheck(config,modules,verbose,nagiosMode,nprocs):
    """ Creates the API check tasks as part of a process pool """

    # a list to keep track of the APIworker objects to call the run() method on
    worker_objects = []

    # store the results
    return_results = []

    # set to warning if something goes wrong
    rc = NagiosStatus.WARNING

    # Use a queue to keep track of the results
    queue = multiprocessing.Queue()

    # Each tuple in the config list is for a single subsystem
    for subIndex,(oid,sub,production,auth) in enumerate(config):
        controller_names = []

        # split the subsystem string to controller names
        controller_names = splitSubName(sub)

        for con in controller_names:
            # check that we haven't prepped a worker on this subsystem already
            try:
                if worker_objects[subIndex]:
                    break
            except IndexError:
                pass
            
            # get the IP for this controller
            try:
                ip = socket.getaddrinfo(con, None)[0][4][0]
            except socket.gaierror:
                # if that fails continue to the next controller
                if not nagiosMode:
                  sys.stdout.write("Could not resolve host name: %s\n"%con)
                continue

            # This is the dictionary struct that holds necessary information
            # for starting the API context
            controller = {}
            controller['sub_name'] = sub
            controller['production'] = production
            controller['ip'] = ip
            controller['auth'] = auth
            
            if verbose:
                print "Calling SFA API check for %s"%ip

            worker_objects.append(APIworker(controller,modules,verbose,nagiosMode))
  
    if len(worker_objects) == 0:
        # pack return results
        name = "None"
        ret_str = "No valid hosts to check"
        rc = NagiosStatus.UNKNOWN
        return_results.append((name,ret_str,rc))
        return return_results

    # A worker pool of at most nprocs workers
    # if the number of subsystems in config is less, then only start the needed number
    pool = multiprocessing.Pool(min(nprocs,len(worker_objects)))

    # start processes
    results = [ (w.controller['sub_name'],pool.apply_async(run, args=(w,), callback = queue.put)) for w in worker_objects ]
    # implement a timeout for all threads
    start_time = time.time()
    timeout_expired = False
    timeout=300
    start_idx = 0

    while (len(results) > 0):
        current_time = time.time()
        if (current_time > (start_time + timeout)):
            timeout_expired = True

        for idx,(name,r) in enumerate(results):
            current_idx = idx + start_idx
            if r.ready():
                # this worker has data ready. we are relying on the
                # callback method to put the data in the queue.
                # assuming successful, we can now stop checking for this one
                ( worker_ret_str, worker_rc ) = r.get()


                # before using the resuls in the return from this function, parse out the first
                # controller name
                return_results.append((name,worker_ret_str, worker_rc))
                results.remove((name,r))
                continue
            elif timeout_expired:
                return_results.append((name, "SFA check of controller timed out", 3))
                r.terminate()
                results.remove((name,r))
        if timeout_expired:
            #print "times up!"
            break
        if results:
            time.sleep(10)

    # return the highest value
    return return_results

def verifyModules(moduleList):
    """ Make sure the users input contains valid (implemented) modules """
    global implementedModules
    for singleModule in moduleList:
        if not singleModule in implementedModules:
            print "Error: unrecognized module: %s"%(singleModule)
            print "Modules implemented are: %s"%(','.join(implementedModules))
            return 1
    return 0

def main():
    """ Main routine:

        1. Parse options/arguments
        2. Input verification on modules
        3. Call sfaAPICheck function
    """

    global defaultConfig
    parser = ArgumentParser()
    parser.add_argument('subsystems',metavar='conA,conB', type=str, nargs='*',
                      help="List of subsystems to run the health check on. Alternate controllers are comma-separated")
    parser.add_argument('-m', '--modules',metavar='mod',type=str, nargs='+', 
                      help="list check modules to run on the hosts")
    parser.add_argument('--production', help="Specific production subsystems to run all pool settings checks", default = True)
    parser.add_argument('-n', '--nprocs', help="Max number of worker processes (for each subsystem)", default = 8)
    parser.add_argument('-c', '--config', help="Path to configuration file", default=defaultConfig)
    parser.add_argument('-p', '--password', help="API password", default="user")
    parser.add_argument('-u', '--username', help="API username", default="user")
    parser.add_argument('-x', '--extended', help="Extended output mode for running from console (implies -q)", action="store_true",default=False)
    parser.add_argument('-v', '--verbose', help="Be verbose", action="store_true",default=False)
    parser.add_argument('-q', '--quiet', help="Redirect stderr to /dev/null", action="store_true",default=False)

    try:
        args = parser.parse_args()
    except ArgumentError:
        parser.print_help()
        return 1
    
    if args.subsystems:
        if not args.password:
            import getpass   
            apipass = getpass.getpass("API Pass: ")

        auth = (args.username,args.password)

        config = [] 
        for idx,sub in enumerate(args.subsystems):
            config_line = ( idx, sub, args.production, auth )
            config.append(config_line)

    else:
        config = readConfig(args.config)

    if not args.modules:
        modules = implementedModules
    else:
        modules = args.modules
    rc = verifyModules(modules)
    if rc != 0:
       parser.print_help()
       return rc
 
    if args.extended:
        nagiosMode = False
    else:
        args.quiet = True
        args.verbose = False
        nagiosMode = True
       
    if args.quiet:
        devnull = open(os.devnull, 'w')
        sys.stderr = devnull

    nprocs = int(args.nprocs)
    return_results = sfaAPICheck(config,modules,args.verbose,nagiosMode,nprocs)
    # the results we get back are a tuple with the controller name that returned the results
    # the output as a list, and the numeric return code
    for con_name,con_ret_str,con_rc in return_results:
        print con_ret_str
        if con_rc > rc:
            rc = con_rc

    return rc

if __name__ == "__main__":
    sys.exit(main())
