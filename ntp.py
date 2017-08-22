import sys
from time import time
import ntplib

ntp_server = 'pool.ntp.org'
version = 3
times = 5
drift = None


def printL(*_):
    pass  # TODO: Clean this later


def get_ntp_response():
    try:
        c = ntplib.NTPClient()
        response = c.request(ntp_server, version=version)
    except Exception as ex:
        printL((' Failed to Get NTP timing '))
        printL((' Reason - ', str(ex)))
        sys.exit(0)
        return None
    return response


def getNTP():
    ntp_timestamp = 0
    response = get_ntp_response()
    if response:
        ntp_timestamp = int(response.tx_time)
    return ntp_timestamp


def setDrift():
    global drift
    response = get_ntp_response()
    if not response:
        return response
    drift = response.offset


def getTime():
    global drift
    curr_time = drift + int(time())
    return curr_time
