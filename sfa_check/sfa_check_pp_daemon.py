#!/usr/bin/python -u
# -u is important for unbuffered STDIN and STDOUT

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
  sfa_api_check_pp_daemon.py
"""

import snmp_passpersist as snmp
import ornl_sfa_check as sfaCheck
import syslog, sys, time, errno, re, socket, os

# General stuff
POLLING_INTERVAL=300	# Update timer, in second
MAX_RETRY=10	# Number of successive retries in case of error
OID_BASE=".1.3.6.1.4.1.341.49.1"

# Global vars
pp = None
config_file = "/usr/local/etc/sfa_check.conf"

def getSNMPstr(snmp_str):
    ret_str = None
   
    if snmp_str == "NONE":
        return None
    s = re.search(".*STRING\\n(.*)",snmp_str)
    if s:
        ret_str=s.group(1)

    return ret_str

def getSNMPint(snmp_int):

    ret_int = -1
   
    if snmp_int == "NONE":
        return None
    s = re.search(".*INTEGER\\n(\d+)",snmp_int)
    if s:
        try:
            ret_int=int(s.group(1))
        except TypeError:
            ret_int = -1

    return ret_int

def getOID(string):
    result=".".join([ str(ord(s)) for s in string ])
    return "%s." % (len(string)) + result

def checkForOldData(oid,timeout):
    global pp

    old_time_str = pp.get(oid + '.3')
    old_time = getSNMPstr(old_time_str)
    if old_time:
        old_time = int(old_time)
    else:
        # if there is not a valid old_time, then we can't tell what we're looking at
        # moving on...
        return 0

    current_time = int(time.time())
    if current_time > (old_time + timeout):
        # it is an old entry
        # get the return code first
        old_rc_str = pp.get(oid + '.1')
        # strip off the junk at the beginning and return an integer
        old_rc = getSNMPint(old_rc_str)
        print old_rc
        if old_rc < 0:
            # if rc could not be parsed from snmp string, status should be UNKNOWN (rc=3)
            old_rc = 3
        elif old_rc == 0:
            # upgrade from OK to WARNING since there was a timeout
            pp.add_int(oid + '.1',1)

        # get the return string
        old_str = pp.get(oid + '.2')
        old_str = getSNMPstr(old_str)
        # handle case if returned None
        if not old_str:
            old_str = "No string at this OID"
            old_rc = 3

        # Preserve the last state
        if re.search("TIMED OUT",old_str):
            m = re.search(".*state: (.*)",old_str)
            if m:
                old_str = m.group(1)
            else:
                old_str = ''
        new_str = "TIMED OUT. Last state: " + old_str
        pp.add_str(oid + '.2',new_str)

    return 0

def update_data():
    """ Runs periodically and spawns APICheck threads """
    global pp

    global config_file


    # set to warning if socket times out
    rc = sfaCheck.NagiosStatus.WARNING

    config = sfaCheck.readConfig(config_file)
  
    fudge = 5
    timeout = POLLING_INTERVAL + fudge

    for oid,sub,production,auth in config:
        con_to_check = sub.split(",")[0]
        oid_prefix = getOID(con_to_check)
        checkForOldData(oid_prefix,timeout)
      
    # this is a Nagios check, so behave as such
    verbose=False
    nagiosMode=True
    modules=sfaCheck.implementedModules
    nprocs=8

    # run the check and place the results in a list
    results = sfaCheck.sfaAPICheck(config,modules,verbose,nagiosMode,nprocs)

    # step through each result
    for sub_name,sub_ret_str,sub_rc in results:
        # place the result under oids trees for both controllers in subsystem
        for con_name in sub_name.split(","):
            #get the oid
            my_oid = getOID(con_name)

            # add the results within the correct portion of the mib
            pp.add_int(my_oid + '.1',sub_rc)
            pp.add_str(my_oid + '.2',sub_ret_str)
            pp.add_str(my_oid + '.3',int(time.time()))
    
    return 0

def main():
  global pp

  syslog.openlog(sys.argv[0],syslog.LOG_PID)

  retry_timestamp=int(time.time())
  retry_counter=MAX_RETRY
  while retry_counter>0:
    try:
      syslog.syslog(syslog.LOG_INFO,"Starting sfa_checkd pass_persist daemon...")

      # Load helpers
      pp=snmp.PassPersist(OID_BASE)

      pp.start(update_data,POLLING_INTERVAL) # Should'nt return (except if updater thread has died)

    except KeyboardInterrupt:
      print "Exiting on user request."
      sys.exit(0)
    except IOError, e:
      if e.errno == errno.EPIPE:
        syslog.syslog(syslog.LOG_INFO,"Snmpd had closed the pipe, exiting...")
        sys.exit(0)
      else:
        syslog.syslog(syslog.LOG_WARNING,"Updater thread as died: IOError: %s" % (e))
    except Exception, e:
        syslog.syslog(syslog.LOG_WARNING,"Main thread as died: %s: %s" % (e.__class__.__name__, e))
    else:
      syslog.syslog(syslog.LOG_WARNING,"Updater thread as died: %s" % (pp.error))

    syslog.syslog(syslog.LOG_WARNING,"Restarting sfa_checkd in 15 sec...")
    time.sleep(15)

    # Errors frequency detection
    now=int(time.time())
    if (now - 3600) > retry_timestamp: # If the previous error is older than 1H
      retry_counter=MAX_RETRY	# Reset the counter
    else:
      retry_counter-=1	# Else countdown
      retry_timestamp=now

  syslog.syslog(syslog.LOG_ERR,"Too many retry, abording...")
  sys.exit(1)


if __name__ == "__main__":
  main()
