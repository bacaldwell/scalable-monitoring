#!/bin/bash

#
#   Copyright 2015 Blake Caldwell, Dustin Leverman
#   Oak Ridge National Laboratory
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

######################
# monitor_ib_health.sh
######################
#
# Maintainer: Blake Caldwell <blakec@ornl.gov>
#
# Purpose: This script is a health check of IB interfaces on the system.
#    It runs the following checks for each interface matching this host
#    in the config file:
#  
#    local_errors_check
#    remote_errors_check
#    pci_health_check
#    sm_lid_check
#    port_active_check
#    port_up_check
#    port_width_check
#    port_speed_check
#
# Where/How/When: Run on command line to query status
#
# Return Values:
#   Nagios return codes:
#   0 -> OK
#   1 -> Warning
#   2 -> Critical
#   3 -> Unknown
#
# Expected Output: string suitable for Nagios paging:
#
# Two interfaces with no errors:
#   mlx4_1-ib0:OK ;; mlx4_1-ib1:OK ;; 
# 
# Two interfaces with IB errors (other end of link has flapped):
#   mlx4_1-ib0:WARNING - Direct-attached ddn-d-1 HCA-1 port 2: [LinkDownedCounter == 48] ;; \
#   mlx4_1-ib1:WARNING - Direct-attached ddn-d-0 HCA-1 port 2: [LinkDownedCounter == 66] ;; 
#
# Assumptions/Dependencies:
#
#   This script expects the config file to be placed at
#     /usr/local/etc/monitor_ib_health.sh
#   and to have a matching entry for this host (say oss4):
#     oss:mlx4_1:1:40
#
#   If node guids are to be mapped to names, populate this file
#     /usr/local/etc/ib_node_name_map.conf
#      [GUID]      "simple name"
#
####################


########################################
# ------------- GLOBALS ---------------#
########################################

IB_HEALTH_PATH=/usr/local/etc
SETPCI=/sbin/setpci

# Set this to 1 to specify the hostname on the command line and to use that
#   to look for the appropriate config file
TESTING=0


##########################################
# ------------- FUNCTIONS ---------------#
##########################################


############################################################
# get_PCI_gen
#
# Returns the expected PCI gen of a PCI bridge based on its
# Vendor and Device Fields
############################################################
get_PCI_gen () {
  case $1 in
    8086:3c04 ) # Intel Corporation Xeon E5/Core i7 IIO PCI Express Root Port 2a
                GEN=3
                ;;
    8086:3c06 ) # Intel Corporation Xeon E5/Core i7 IIO PCI Express Root Port 2a
                GEN=3
                ;;
    * )
      GEN=0
      ;;
  esac
  
  echo $GEN
}


##############################################################
# get_gen_from_speed
#
# Based on the speed string returned from lspci, we can figure
# out what PCI gen it corresponds to
##############################################################
get_gen_from_speed () {
  case $1 in
    "10GT/s" ) GEN=3
               ;;
    "unknown") GEN=3
               ;;
    "5GT/s" ) GEN=2
               ;;
    "2.5GT/s" ) GEN=1
               ;;
    * ) GEN=0
  esac
  echo $GEN
}


##############################################################
# get_saquery_binary
#
# Different OFED versions use different paths for its binaries
# This should cover RHEL5 and RHEL6
##############################################################
get_saquery_binary () {
  return=
  # figure out which saquery to use
  if [ -e /usr/ofed/sbin/saquery ]; then
    return=/usr/ofed/sbin/saquery
  elif [ -e /usr/sbin/saquery ]; then
    return=/usr/sbin/saquery
  else
    MESSAGES+=("Failed to find saquery binary")
    return=
  fi

  echo "$return"
}


##############################################################
# get_ibqueryerrors_binary
#
# Different OFED versions use different paths for its binaries
# This should cover RHEL5 and RHEL6
##############################################################
get_ibqueryerrors_binary () {
  return=
  if [ -e /usr/ofed/sbin/ibqueryerrors ]; then
    return=/usr/ofed/sbin/ibqueryerrors
  elif [ -e /usr/sbin/ibqueryerrors ]; then
    return=/usr/sbin/ibqueryerrors
  else
    MESSAGES+=("Failed to find ibqueryerrors binary")
  fi
  echo "$return"
}


############################################################
# local_errors_check
#
# Run ibqueryerrors on the GUID of the local interface.
############################################################
local_errors_check () {
        # start with a clean slate
        LOCAL_IB_ERRORS=

        IBQUERYERRORS=$(get_ibqueryerrors_binary)
        SUPRESS_LIST="PortRcvErrors,PortXmitDiscards,PortXmitWait,VL15Dropped,PortRcvSwitchRelayErrors"
        IBQUERYERRORS_OPTS="-C $MTHCA_DIR -P $PORT_DIR -s $SUPRESS_LIST"
        # Just be explicit that we are using the default
        IBQUERYERRORS_OPTS+=" --threshold-file=/etc/infiniband-diags/error_thresholds"

        # check if we found a value ibqueryerrors binary
        if [[ -n "$IBQUERYERRORS" ]]; then
              IBQUERYERRORS_OPTS="-C $MTHCA_DIR -P $PORT_DIR -s $SUPRESS_LIST"
              # Just be explicit that we are using the default
              IBQUERYERRORS_OPTS+=" --threshold-file=/etc/infiniband-diags/error_thresholds"

              # only check the local interface with -G GUID
              GID=$(cat $SYSFS_DIR/gids/0)
              GUID=$(echo $GID|sed \
                's/\w\{4\}:\w\{4\}:\w\{4\}:\w\{4\}:\(\w\{4\}\):\(\w\{4\}\):\(\w\{4\}\):\(\w\{4\}\)/0x\1\2\3\4/')
              IBQUERYERRORS_OPTS+=" -G $GUID"

              # run ibqueryerrors and clean up the output
              ERRORS=
              ERRORS=$($IBQUERYERRORS $IBQUERYERRORS_OPTS|grep -A1 "Errors for"| \
                 sed ':a;N;$!ba;s/\n/ /g'|sed 's/   GUID/GUID/'|sed 's/Errors/IB HCA errors/')
              if [ "$?" -eq "-1" ]; then
                LOCAL_IB_ERRORS=warning
                MESSAGES+=("Failed to query HCA for errors")
              elif [ -n "$ERRORS" ]; then
                LOCAL_IB_ERRORS=warning
                MESSAGES+=("$ERRORS")
              else
                LOCAL_IB_ERRORS=ok
              fi
        else
          LOCAL_IB_ERRORS=warning
        fi
        CHECKS+=($LOCAL_IB_ERRORS)
}


############################################################
# remote_errors_check
#
# use saqeury to get the LID and port of the other end of the
# link and 
############################################################
remote_errors_check () {
        # start with a clean slate
        REMOTE_IB_ERRORS=

        SAQUERY=$(get_saquery_binary)
        IBQUERYERRORS=$(get_ibqueryerrors_binary)
        SUPRESS_LIST="PortRcvErrors,PortXmitDiscards,PortXmitWait,VL15Dropped,PortRcvSwitchRelayErrors"
        IBQUERYERRORS_OPTS="-C $MTHCA_DIR -P $PORT_DIR -s $SUPRESS_LIST"
        # Just be explicit that we are using the default
        IBQUERYERRORS_OPTS+=" --threshold-file=/etc/infiniband-diags/error_thresholds"

        if [[ -n "$SAQUERY" ]] && [[ -n "$IBQUERYERRORS" ]]; then
          # add the node-name-map into the SAQUERY command
          SAQUERY_OPTS="-C $MTHCA_DIR -P $PORT_DIR"
          SAQUERY_OPTS+=" --node-name-map /usr/local/etc/ib_node_name_map.conf"
          SAQUERY+=" $SAQUERY_OPTS"

          REMOTE=$($SAQUERY -x $LID 2> /dev/null)
          # we have to send all stderr to /dev/null in case the node-name-map could not be found

          REMOTE_LID=$(echo "$REMOTE" |grep ToLID|sed 's/.*\.\([0-9]\+\)$/\1/')
          REMOTE_PORT=$(echo "$REMOTE" |grep ToPort|sed 's/.*\.\([0-9]\+\)$/\1/')
          if [[ -n "$REMOTE_LID" ]] && [[ -n $REMOTE_PORT ]]; then

            # we got back a result from saquery, now run saquery again to get the GUID
            REMOTE_INFO=$($SAQUERY $REMOTE_LID 2> /dev/null)
            REMOTE_DESC=$(echo "$REMOTE_INFO"|grep NodeDescription |sed 's/.*\.\([^\.].*\)$/\1/')
            REMOTE_GUID=$(echo "$REMOTE_INFO"|grep port_guid |sed 's/.*\.\([^\.].*\)$/\1/')
            if [[ -n "$REMOTE_GUID" ]]; then
              IBQUERYERRORS_OPTS+=" -G $REMOTE_GUID"

              # prepare the output messages
              # if we are the subnet manager, that means this is a direct-attached device
              shopt -s nocasematch
              if [[ "$LID" == "$SM_LID" ]] && [[ "$IS_SM" != "SM" ]]; then
                output_message="Direct-attached "
              else
                output_message="IB Switch Port "
              fi
              shopt -u nocasematch

              if [[ -n "$REMOTE_DESC" ]]; then
                output_message+="$REMOTE_DESC"
              else
                output_message+="$REMOTE_GUID"
              fi

              ERRORS=
              ERRORS=$($IBQUERYERRORS $IBQUERYERRORS_OPTS|grep " port ${REMOTE_PORT}:" | \
                sed "s/   GUID 0x[0-9a-f]\+ /$output_message /")
              if [ "$?" -eq "-1" ]; then
                REMOTE_IB_ERRORS=warning
                MESSAGES+=("Failed to query remote node for errors")
              elif [ -n "$ERRORS" ]; then
                REMOTE_IB_ERRORS=warning
                MESSAGES+=("$ERRORS")
              else
                FABRIC_ERRORS=ok
              fi
            fi
          fi
        fi
        CHECKS+=($REMOTE_IB_ERRORS)
}


############################################################
# pci_health_check
#
# This checks that the PCI link width is 4x and the speed is
# right
############################################################
pci_health_check () {        
        PCI_SPEED_STATUS=''
        PCI_WIDTH_STATUS=''

        # RHEL6 systems put all the information in this link at the top level
        RHEL6_TEST=$(readlink /sys/class/infiniband/${MTHCA_DIR})
        if [ -z $RHEL6_TEST ]; then
          # not RHEL6
          # The device symlink points to the pci dev identifier
          DEVPATH=$(readlink /sys/class/infiniband/${MTHCA_DIR}/device)
        else
          DEVPATH=$(echo $RHEL6_TEST| sed 's/\(.*\)\/infiniband.*/\1/')
        fi

        # We are interested in the last identifier in the tree
        PCI_DEV=$(basename $DEVPATH)

        # The bridge id is the 2nd to last identifier in the tree
        PCI_BRIDGE=$(basename $(dirname $DEVPATH))
        PCI_CAP_WIDTH=$(((0x$($SETPCI -s $PCI_DEV CAP_EXP+0x0c.w) & 0x3f0) >> 4 ))
        PCI_STA_WIDTH=$(((0x$($SETPCI -s $PCI_DEV CAP_EXP+0x12.w) & 0x3f0) >> 4 ))
        PCI_CAP_SPEED=$((0x$($SETPCI -s $PCI_DEV CAP_EXP+0x0c.w) & 0xf ))
        PCI_STA_SPEED=$((0x$($SETPCI -s $PCI_DEV CAP_EXP+0x12.w) & 0xf ))
        PCI_BRIDGE_WIDTH=$(((0x$($SETPCI -s $PCI_BRIDGE CAP_EXP+0x0c.w) & 0x3f0) >> 4 ))
        PCI_BRIDGE_SPEED=$((0x$($SETPCI -s $PCI_BRIDGE CAP_EXP+0x0c.w) & 0xf ))
        if [[ "$PCI_STA_SPEED" != "$PCI_CAP_SPEED" ]]; then
            if [[ "$PCI_STA_SPEED" != "$PCI_BRIDGE_SPEED" ]]; then
              MESSAGES+=("PCI SPEED gen ${PCI_STA_SPEED}, capability gen ${PCI_CAP_SPEED}")
              PCI_SPEED_STATUS=warning
            else

              # PCI device limited by PCI bridge gen
              PCI_SPEED_STATUS=ok
            fi
        else
            PCI_SPEED_STATUS=ok
        fi  

        if [[ "$PCI_STA_WIDTH" != "$PCI_CAP_WIDTH" ]]; then
            if [[ "${PCI_STA_WIDTH}" != "${PCI_BRIDGE_WIDTH}" ]]; then
                MESSAGES+=("PCI WIDTH x${PCI_STA_WIDTH}, capability x${PCI_CAP_WIDTH}")
                PCI_WIDTH_STATUS=warning
            else
                MESSAGES+=("Current width is x${PCI_STA_WIDTH}. Limited by PCI bridge width x${PCI_BRIDGE_WIDTH}")
                PCI_SPEED_STATUS=ok
            fi
        else
            PCI_WIDTH_STATUS=ok
        fi

        CHECKS+=($PCI_SPEED_STATUS)
        CHECKS+=($PCI_WIDTH_STATUS)
}


############################################################
# sm_lid_check
#
# This checks to see if the lid is something other than 0x00
############################################################
sm_lid_check () {
        SM_LID_STATUS=''

        if [ "$SM_LID" == "0x00" ]; then
          MESSAGES+=("SM lid is 0x00: Invalid")
          SM_LID_STATUS=critical
        else
          SM_LID_STATUS=ok
        fi
        CHECKS+=($SM_LID_STATUS)
}


##########################################
# port_active_check()
#
# This checks to see if the port is active
##########################################
port_active_check () {
        STATE_STATUS=''

        STATE=$(cat $SYSFS_DIR/state | awk '{print $2}')

        if [ "$STATE" == "ACTIVE" ]; then 
          STATE_STATUS=ok
        else 
          MESSAGES+=("IB State $STATE")
          STATE_STATUS=critical
        fi

        CHECKS+=($STATE_STATUS)
}


############################################################
# port_up_check()
#
# This checks to see if the physical state of the port is up
############################################################
port_up_check () {
        PHYS_STATE_STATUS=''

        PHYS_STATE=$(cat $SYSFS_DIR/phys_state | awk '{print $2}')
        if [ "$PHYS_STATE" == "LinkUp" ]; then
          PHYS_STATE_STATUS=ok
        else
          MESSAGES+=("IB Physical State: $PHYS_STATE")
          PHYS_STATE_STATUS=critical
        fi

        CHECKS+=($PHYS_STATE_STATUS)
}


################################################################
# port_width_check ()
#
# This checks that the link width is 4x
################################################################
port_width_check () {
        LINK_WIDTH_STATUS=''
        LINK_WIDTH=$(cat $SYSFS_DIR/rate | sed 's/.*(\(\w\+\).*)/\1/')

        if [ "$LINK_WIDTH" == "4X" ]; then
          LINK_WIDTH_STATUS=ok
        else
          MESSAGES+=("Link Width: $LINK_WIDTH")
          LINK_WIDTH_STATUS=critical
        fi
        CHECKS+=($LINK_WIDTH_STATUS)
}


################################################################
# port_speed_check ()
#
# This tests to see if the speed of the actual speed of the port 
# matches what you expect to see, which was input from the .conf
################################################################
port_speed_check () {
        LINK_RATE_STATUS=''
        LINK_RATE=$(cat $SYSFS_DIR/rate | awk '{print $1}')

        case $(echo $CONNECTION | tr '[:lower:]' '[:upper:]') in
            56) if [ "$LINK_RATE" == "56" ];then
                  LINK_RATE_STATUS=ok
                else
                  MESSAGES+=("Link rate: ${LINK_RATE}Gbps is inappropriate for FDR IB")
                  LINK_RATE_STATUS=critical
                fi
                ;;
            40) if [ "$LINK_RATE" == "40" ];then
                  LINK_RATE_STATUS=ok
                else
                  MESSAGES+=("Link rate: ${LINK_RATE}Gbps is inappropriate for QDR IB")
                  LINK_RATE_STATUS=critical
                fi
                ;;
            20) if [ "$LINK_RATE" == "20" ];then 
                  LINK_RATE_STATUS=ok
                else
                  MESSAGES+=("Link rate: ${LINK_RATE}Gbps is inappropriate for DDR IB")
                  LINK_RATE_STATUS=critical
                fi
                ;;
            10) if [ "$LINK_RATE" == "10" ];then
                  LINK_RATE_STATUS=ok
                else
                  MESSAGES+=("Link rate: ${LINK_RATE}Gbps is inappropriate for SDR IB")
                  LINK_RATE_STATUS=critical
                fi
                ;;
            * ) MESSAGES+=("Link rate in config file: $(echo $CONNECTION | \
                  tr '[:lower:]' '[:upper:]') unrecognized")
                LINK_RATE_STATUS=critical
                ;;
            esac
        CHECKS+=($LINK_RATE_STATUS)
}


########################################################
# ----------------- MAIN PROGRAM ----------------------#
########################################################

if [ $TESTING == "1" ]; then
  if [ $# -eq 1 ]; then
    host=$1
  else  
    echo "Testing mode usage: monitor_ib_health.sh [hostname]"
    echo "To turn off, set TESTING to 0"
    exit 3
  fi
else
    host=$(hostname -s)
fi

SPECIFIC_MATCH="^$host:"
GENERAL_MATCH="^$(echo $host|sed 's/\([^0-9]*\)[0-9]\+/\1/'):"
VERY_GENERAL_MATCH="^$(echo $host|sed 's/\(.*oss\)[0-9]\+[a-i][0-9]$/\1/'):"

# Now set the appropriate configuration file and test that it exists
CONFIGFILE="$IB_HEALTH_PATH/monitor_ib_health.conf"
DEVICES=()

grep -q "$SPECIFIC_MATCH" $CONFIGFILE
if [ $? -eq 0 ]; then
  PATTERN=$SPECIFIC_MATCH
else
  grep -q "$GENERAL_MATCH" $CONFIGFILE
  if [ $? -eq 0 ]; then
    PATTERN=$GENERAL_MATCH
  else
    # try the pattern for OSS (number-letter-number)
    PATTERN=$VERY_GENERAL_MATCH
  fi
fi

if [ -f $CONFIGFILE ]; then
    while read -r line
    do
       DEV=
       DEV=$(echo $line|grep "$PATTERN")
       if [ -n "$DEV" ]; then
         DEVICES+=("$DEV")
       fi
    done < "$CONFIGFILE"
else
    # CONFIGFILE does not exist, return critical
    echo "No configuration file: $CONFIGFILE"
    exit 3
fi

# Set default return code to 0 and then override if there are errors
EXIT=0

# Go through each line of CONFIGFILE to run ib health checks
for i in $(seq 0 $(( ${#DEVICES[@]} - 1 ))); do

        # start with no status results
        CHECKS=()

        # Set default return code for this interface to 3 (UNKNOWN).
        # This works because we don't have any checks that return unknown.
        # Unknown means there was an error with the process of running
        # the checks
        THIS_IF=3

        SKIP=0
        # keep messages to append to ouput line in an array
        MESSAGES=()

        # Parse config file
        MTHCA_DIR=$(echo ${DEVICES[$i]} | awk -F: '{print $2}')
        PORT_DIR=$(echo ${DEVICES[$i]} | awk -F: '{print $3}')
        CONNECTION=$(echo ${DEVICES[$i]} | awk -F: '{print $4}')
        IS_SM=$(echo ${DEVICES[$i]} | awk -F: '{print $5}')

        # this is needed by a lot of checks
        SYSFS_DIR="/sys/class/infiniband/$MTHCA_DIR/ports/$PORT_DIR"
        LID=$(cat $SYSFS_DIR/lid)
        SM_LID=$(cat $SYSFS_DIR/sm_lid)

        # does the interface even exist on the machine?
        if [ ! -d /sys/class/infiniband/$MTHCA_DIR ]; then
          MESSAGES+=("$MTHCA_DIR interface does not exist")
          SKIP=1
        elif [ ! -d /sys/class/infiniband/$MTHCA_DIR/ports/$PORT_DIR ]; then
          MESSAGES+=("Port $PORT_DIR does not exist on $MTHCA_DIR")
          SKIP=1
        fi

        if [ "$SKIP" -eq "0" ]; then
          # always run the pci health check no matter the state of the IB connection
          pci_health_check
          port_active_check
          port_up_check
          if [[ "${CHECKS[$(( ${#CHECKS[@]} - 1 ))]}" != "critical" ]] &&
             [[ "${CHECKS[$(( ${#CHECKS[@]} - 2 ))]}" != "critical" ]]; then
            # check that the last two check results were ok before proceeding
            # check that the last check result for port_up_check was ok
            sm_lid_check
            port_width_check
            port_speed_check
            local_errors_check
            shopt -s nocasematch
            if [[ "$LID" == "$SM_LID" ]] && [[ "$IS_SM" != "SM" ]]; then
              # this is a direct attached host
              remote_errors_check
            fi
            shopt -u nocasematch
          fi

          # iterate through all checks. If some weren't run, then conveniently, there 
          # will be no results for the for loop below
          # If the status's are all ok, then exit "ok",
          # otherwise exit critical
          for result in ${CHECKS[@]}; do
            if [ "$result" == "warning" ]; then
                # set to warning if 0 (OKAY) or 3 (UNKNOWN)
                THIS_IF=1
            elif [ "$result" == "critical" ]; then
                # set return to CRITICAL and stop
                THIS_IF=2
                break
            elif [ "$result" == "ok" ]; then
                if [ "$THIS_IF" -eq "3" ]; then
                    # only override unknown (default)
                    THIS_IF=0
                fi
            fi

          done
        else
          # If we are skipping this device because it could not be found, exit critical
          THIS_IF=2
        fi

        # change PORT_DIR to ib0/ib1
        if [ "$PORT_DIR" -eq "1" ]; then
          PORT_DIR=ib0
        elif [ "$PORT_DIR" -eq "2" ]; then
          PORT_DIR=ib1
        fi

        if [ "$THIS_IF" -eq 0 ]; then
          /bin/echo -n "$MTHCA_DIR-$PORT_DIR:OK"
          for i in "${MESSAGES[@]}"; do
              /bin/echo -n " - $i"
          done
          /bin/echo -n " ;; "
        else
          if [ "$THIS_IF" -eq 1 ]; then
            /bin/echo -n "$MTHCA_DIR-$PORT_DIR:WARNING"
            # a warning will override a 0 or 1
            if [ "$EXIT" -lt "2" ]; then
              EXIT=1
            fi
          elif [ "$THIS_IF" -eq 2 ]; then
            /bin/echo -n "$MTHCA_DIR-$PORT_DIR:CRITICAL"
            # will always override
            EXIT=2
          elif [ "$THIS_IF" -eq 3 ]; then
            /bin/echo -n "$MTHCA_DIR-$PORT_DIR:UNKNOWN"
            # an unknown will override a 0,1, or 3, but not a 2
            if [ "$EXIT" -ne "2" ]; then
              EXIT=3
            fi
          fi
          for i in "${MESSAGES[@]}"; do
            /bin/echo -n " - $i"
          done

          # print where we are connected to for tracing down the cable
          if [[ "$STATE_STATUS" == "ok" ]] &&
             [[ "$PHYS_STATE_STATUS" == "ok" ]]; then

            SAQUERY=$(get_saquery_binary)
            if [[ -n "$SAQUERY" ]]; then
              # add the node-name-map into the SAQUERY command
              SAQUERY_OPTS="-C $MTHCA_DIR -P $PORT_DIR"
              SAQUERY_OPTS+="--node-name-map /usr/local/etc/ib_node_name_map.conf"
              # we have to send all stderr to /dev/null in case the ib_node_name_map
              # could not be found
              SAQUERY+=" $SAQUERY_OPTS"

              REMOTE=$($SAQUERY -x $LID 2> /dev/null)
              REMOTE_LID=$(echo "$REMOTE" |grep ToLID|sed 's/.*\.\([0-9]\+\)$/\1/')
              REMOTE_PORT=$(echo "$REMOTE" |grep ToPort|sed 's/.*\.\([0-9]\+\)$/\1/')
              if [[ -n "$REMOTE_LID" ]] && [[ -n $REMOTE_PORT ]]; then
                REMOTE_INFO=$($SAQUERY $REMOTE_LID 2> /dev/null)
                REMOTE_DESC=$(echo "$REMOTE_INFO"|grep NodeDescription | \
                  sed 's/.*\.\([^\.].*\)$/\1/')
                REMOTE_GUID=$(echo "$REMOTE_INFO"|grep node_guid |sed 's/.*\.\([^\.].*\)$/\1/')
                if [[ -n "$REMOTE_DESC" ]]; then
                  /bin/echo -n " - Connected to ${REMOTE_DESC} Port ${REMOTE_PORT}"
                elif [[ -n "$REMOTE_GUID" ]]; then
                  /bin/echo -n " - Connected to ${REMOTE_GUID} Port ${REMOTE_PORT}"
                else
                  /bin/echo -n " - Connected to LID ${REMOTE_LID} Port ${REMOTE_PORT}"
                fi
              fi
            fi
          fi
          /bin/echo -n " ;; "
        fi

done
echo "" 
exit $EXIT
