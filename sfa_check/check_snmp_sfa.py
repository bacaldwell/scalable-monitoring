#!/usr/bin/python -Ott

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

""" Brief summary of the script

Maintainer: Blake Caldwell <blakec@ornl.gov>
Purpose: Return status of DDN SFA subsystem from quering management server via SNMP
Where/How/When: Nagios Plugin
Return Values: 0 - Ok, 1 - Warning, 2 - Critical, 3 - Unknown
Expected Output: Check status line
Assumptions/Dependencies: passpersist daemon running on management server 
              populates OIDs with information collected from sfa_check.py

Credit: SNMP passpersist usage and code examples from
        https://dreness.com/wikimedia/index.php?title=Net_SNMP
"""
import sys
import logging
import logging.handlers
from optparse import OptionParser, OptionError
import subprocess
import re

BASE_OID = ".1.3.6.1.4.1.341.49.1"
LOGGER = None

def stringToOID(string):
    result=".".join([ str(ord(s)) for s in string ])
    return "%s." % (len(string)) + result

def main():
    """main subroutine"""
   
    parser = OptionParser()

    parser.add_option("-v", "--verbose", help="Be verbose", action="count")
    parser.add_option('-C', "--community", help="community name", dest="community")
    parser.add_option('-H', "--hostname", help="Name or IP address of specific host to check", dest="hostname")
    parser.add_option("--controller", help="Name of controllers to check", dest="controller")
    parser.add_option('-P', "--port", help="SNMP Port. Default is 161", default='161', dest="port")
    parser.add_option('-V', "--version", help="Chooses version number. e.g. 1, 2c, 3", default='2c', dest="version")
    # add parser.add_option() calls here
    try:
        (options, args) = parser.parse_args()
    except OptionError:
        parser.print_help()
        return 3
 
    if options.controller:
        if not re.match("\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",options.controller):
            # get short hostname if not in dotted quad notation
            m = re.search("(.*?)\.",options.controller)
            # if hostname can be shortened
            if m: controller = m.group(1)
            # else it already is short
            else: controller = options.controller
        else:
            controller = options.controller
    else:
        print "No controller to check specified"
        return 3

    encoded_controller = stringToOID(controller)
 
    # store arguments in variables
    snmp_opts=({'community': options.community, 'version': options.version, 'host':options.hostname, 'port':options.port})
  
    retcode_oid = "1"
    output_oid = "2"
    timestamp_oid = "3"

    oid = "%s.%s.%s"%(BASE_OID,encoded_controller,retcode_oid)
    check_return = getSNMPValue(snmp_opts,oid)
    oid = "%s.%s.%s"%(BASE_OID,encoded_controller,output_oid)
    output = getSNMPValue(snmp_opts,oid)
    print output
    return int(check_return)

def getSNMPValue(snmp_opts,oid):
    rc, stdout, stderr = snmp(snmp_opts,'get',oid)
    # Fail if we can't get results
    if rc != 0:
        print stderr
        sys.exit(3)
    elif "No Such Instance currently exists at this OID" in stdout:
        print(stdout)
        sys.exit(3)
    value = re.match('(?:.+)::(?:.+)\.(?:\d+) \= .+?\: (.+)', stdout).groups()[0]
    return value

def snmp(opts,type,oid):
    """returns return code, stdout, and stderr"""
    if type == 'get':
        cmd = '/usr/bin/snmpget'
    else:
        cmd = '/usr/bin/snmpwalk'

    p = subprocess.Popen(
          [cmd,'-Le','-c',opts['community'],'-v',opts['version'],opts['host']+':'+opts['port'],oid],
          stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    returncode=p.wait()
    stdout, stderr = p.communicate()
    stdout = stdout.strip('\n') # removes trailing new line
    stderr = stderr.strip('\n') # removes trailing new line
 
    return (returncode, stdout, stderr)

if __name__ == "__main__":
    sys.exit(main())
