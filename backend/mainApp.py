#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import datetime
import time
import requests
import json
#from unittest import result

import mysql.connector
import eventlet

eventlet.monkey_patch()
import socket
import ari
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, request, render_template, Response, abort
from flask_socketio import SocketIO, send, emit, disconnect
from requests.exceptions import HTTPError, ConnectionError

AppPath = sys.path[0]
sys.path.append(AppPath+'/mainApplibs/')
import siptoid

LOG_FILE = AppPath + "/st.log"
logging.basicConfig(level=logging.INFO)

log = logging.getLogger()
handler = RotatingFileHandler(LOG_FILE, maxBytes=2*1024*1024, backupCount=40)

log_formatter = logging.Formatter(
    '%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')
handler.setLevel(logging.INFO)
handler.setFormatter(log_formatter)
log.addHandler(handler)

disposition_convert = {'User busy': 'BUSY', 'User alerting, no answer': 'BUSY'}


app = Flask(__name__)
app.config['SECRET_KEY'] = 'xxxxx'
socketio = SocketIO(app)  # , async_mode='eventlet')

pool = eventlet.GreenPool()
ARIclient = ari.connect('http://localhost:3766/',
                        'servApiTester', 'xxxxxxxxx')

                        
DB_lock = False
Allcl_lock = False
Allcl = dict()
EndpointEvents = dict()
IpWhiteList = []
TotallCalls = 0

class AutoClient:

    def __init__(self, sid, endpoint, cid, pinCode):
        self.autoCall = True
        self.incoming = False
        self.timeout = 59
        self.sid = sid
        self.cid = cid
        self.connected = False
        self.bridgeUP = False
        self.holding_bridge = None
        self.announcer_timer = None
        self.endpoint = endpoint
        self.bridge = None
        self.bridgeID = '44444-55555-' + endpoint
        self.channel0 = None
        self.channel1 = None
        self.channel0_ID = '44444.' + self.endpoint
        self.channel1_ID = None
        self.channel0_disposition = None
        self.channel1_disposition = None
        self.channel1_wrongDest = False
        self.pinCode = pinCode
        self.pinCodeTMP = ''
        self.pinCodeAccepted = False
        self.recording = None
        self.MOHenabled = False
        self.dst = ''
        self.recFileName = ''
        self.recPath = ''
        self.beep = 'false'
        self.start = datetime.datetime.now()
        self.answer = None
        self.end = None
        self.lastData = None
        self.mediaExt = '.ogg'
        self.uniqueid = None
        self.ip = None
        self.queueList = None
        self.queueListCntr = 0
        self.queueStop = False
        self.queueClientUnavailable = False
        self.trunk = None

def CallEnd(cl):
    print "cl.end,  cl.answer__________________________________________", cl.end,  cl.answer
    #cl = Allcl[clientSid]
    postInfo = {}
    postInfo['RecordingStatus'] = 'completed'
    postInfo['RecordingSid'] = cl.recFileName
    postInfo['RecordingUrl'] = 'http://media.mydomain:88/autoDial' + cl.recPath + cl.mediaExt
    postInfo['CallSid'] = cl.uniqueid  # cl.channel1_ID
    postInfo['AccountSid'] = 0
    postInfo['RecordingDuration'] = (cl.end - cl.answer).total_seconds()
    postInfo['fromphone'] = cl.endpoint
    postInfo['tophone'] = cl.dst  # event_json['channel']['dialplan']['exten']
    postInfo['time'] = time.time()  # cl.uniqueid
    print '________________                 __________CallEnd  postInfo:  ', postInfo
    #r = requests.post('https://crm.mydomain/callend.php', data=postInfo, verify=False)
    #print '___________________              _______CallEnd  result: ', r

########################################################### ARI Events


def ChannelEnteredBridge(bridge, event):
    # print json.dumps(event, sort_keys=True, indent=4)
    channel = event.get('channel')
    channel_count = len(bridge.json.get('channels'))
    channels = bridge.json.get('channels')
    clientSid = GetClientByBridgeID(bridge, event)

    if clientSid == None:
        return
    cl = Allcl[clientSid]
    BroadcastEvent(cl.endpoint, event)
    #if cl.endpoint == '211': log.info("ChannelEnteredBridge cl.endpoint = %s, channel_count = %s",cl.endpoint, channel_count)
    if channel_count == 2:
        cl.bridgeUP = True
        now = datetime.datetime.now()
        cl.answer = now
        cl.recFileName = str(now.day) + str(now.month) + str(now.year) + str(now.hour) + str(now.minute) + str(
            now.second) + '_' + cl.dst
        cl.recPath = '/' + str(now.year) + '/' + str(now.month) + '/' + str(
            now.day) + '/' + cl.endpoint + '/' + cl.recFileName
        print "ChannelEnteredBridge, cl.recFileName________________________________", cl.recFileName
        if channels[1] == cl.channel1_ID:  # /mnt/v50/monitor1/tst
            cl.recording = ARIclient.bridges.record(bridgeId=cl.bridgeID,
                                                    name=cl.recPath,
                                                    format='ogg',
                                                    beep=cl.beep,
                                                    ifExists='overwrite')
    print channel_count, " Channel %s Entered bridge %s" % (channel.get('name'), bridge.id)


def ChannelLeftBridge(bridge, event):
    # print json.dumps(event, sort_keys=True, indent=4)
    channel = event.get('channel')
    channel_count = len(bridge.json.get('channels'))
    #channels = bridge.json.get('channels')

    print channel_count, " Channel %s Left bridge %s" % (channel.get('name'), bridge.id)

    clientSid = GetClientByBridgeID(bridge, event)
    #print 'after GetClientByBridgeID', clientSid
    if clientSid == None:
        return
    print "______________________clientSid", clientSid
    cl = Allcl[clientSid]
    BroadcastEvent(cl.endpoint, event)

    if channel_count == 1:
        cl.bridge.destroy()


def ChannelVarset(channel, event):
  #print json.dumps(event, sort_keys=True, indent=4)
    clientSid = GetClientByChanID(channel, event)
    if clientSid == None:
        return
    cl = Allcl[clientSid]
    BroadcastEvent(cl.endpoint, event)


def ChannelDialplan(channel, event):
  #print json.dumps(event, sort_keys=True, indent=4)
    clientSid = GetClientByChanID(channel, event)
    if clientSid == None:
        return

    cl = Allcl[clientSid]
    BroadcastEvent(cl.endpoint, event)

    chanID = channel.json.get('id')
    if chanID == cl.channel0_ID:
        xx = ARIclient.channels.dial(channelId=cl.channel0_ID,
                                     # caller=cl.channel0_ID,
                                     timeout='60')


def ChannelDestroyed(channel, event):
    # print json.dumps(event, sort_keys=True, indent=4)
    # log.info(json.dumps(event, sort_keys=True, indent=4))
    cause_txt = event.get('cause_txt')
    clientSid = GetClientByChanID(channel, event)
    if clientSid == None:
        return

    chanID = channel.json.get('id')
    cl = Allcl[clientSid]
    BroadcastEvent(cl.endpoint, event)
    socketio.emit(event['type'], event, room=clientSid)

    if chanID == cl.channel1_ID:
        cl.channel1_disposition = cause_txt
        cl.end = datetime.datetime.now()
        
        if cl.bridgeUP:
            pool.spawn_n(CallEnd, cl)
        if cl.incoming:
            if cl.channel0_disposition is None:
                if "User alerting, no answer" in cl.channel1_disposition: cl.queueClientUnavailable =True
                else: runner3 = pool.spawn_n(WriteCallToDb, cl)
                pool.spawn_n(Queue, cl)
        else:
            runner3 = pool.spawn_n(WriteCallToDb, cl)

        if cl.autoCall:
            StartStopMOH(clientSid)
        if not cl.autoCall and not cl.incoming:
            #print cause_txt
            runner14 = pool.spawn_n(Play_tone, cl, clientSid)
            #if 'busy'in cause_txt: cl.channel0.play(media='tone:busy;tonezone=us')

    if chanID == cl.channel0_ID:
        cl.channel0_disposition = cause_txt
        SafeHangup(cl.channel1_ID)


def ChannelDtmfReceived(channel, event):
  #print json.dumps(event, sort_keys=True, indent=4)
    clientSid = GetClientByChanID(channel, event)
    if clientSid == None:
        return
    socketio.emit(event['type'], event, room=clientSid)

    chanID = channel.json.get('id')
    digit = event['digit']
    cl = Allcl[clientSid]
    BroadcastEvent(cl.endpoint, event)

    if cl.pinCodeAccepted:
        if digit == '0':
            StartStopMOH(clientSid)

    if chanID == cl.channel0_ID:
        if cl.autoCall:
            runner = pool.spawn_n(CheckPINcode, cl, channel, digit)
        # CheckPINcode(Allcl[clientSid], channel, digit)


def ChannelStateChange(channel, event):
  #print json.dumps(event, sort_keys=True, indent=4)
    chanSate = channel.json.get('state')
    chanID = channel.json.get('id')
    clientSid = GetClientByChanID(channel, event)
    if clientSid == None:
        return
    print 'ChannelStateChange, chanSate________________', chanSate, chanID
    cl = Allcl[clientSid]
    BroadcastEvent(cl.endpoint, event)

    if chanSate == 'Ringing':
        if chanID == cl.channel1_ID:
            if not cl.autoCall and not cl.incoming:
                #   ringing, stutter, busy, congestion
                #cl.channel0.play(media='tone:ring;tonezone=us')
                cl.channel0.ring()

    if chanSate == 'Up':
        if chanID == cl.channel0_ID:
            #cl.channel0.answer()
            if cl.autoCall:
                cl.channel0.play(media='sound:agent-pass')

        if chanID == cl.channel1_ID:
            cl.channel0.stopMoh()
            SafeBridgeDestroy(cl.bridge)
            cl.bridge = ARIclient.bridges.create(
                type='mixing', bridgeId=cl.bridgeID)
            if not cl.autoCall and not cl.incoming:
                print 'ChannelStateChange, cl.incoming______________', cl.incoming, chanID
                cl.channel0.answer()
            cl.channel1.answer()
            cl.bridge.addChannel(channel=[cl.channel0_ID, cl.channel1_ID])
    socketio.emit(event['type'], event, room=clientSid)


def stasis_start(channel_obj, event):
    channel = channel_obj.get('channel')
    channel_name = channel.json.get('name')
    channel_id = channel.json.get('id')
    args = event.get('args')
    #print json.dumps(event, sort_keys=True, indent=4)
    if args[0] == 'incoming' or args[0] == 'outgoing':
        if len(args) > 1:
            myEndpoint = str(args[1]).replace('+', '')
            BroadcastEvent(myEndpoint, event)
        # try:

        #     BroadcastEvent(args[1].replace('+', ''),event)
        # except: j=0
    #log.info('stasis_start_______________________%s,%s,%s,%s,%s,%s',args[0],args[1],args[2],args[3],args[4],args[5])
    log.info('stasis_start_______________________%s',args)
    if args[0] == 'incoming':
        #    example args from Stasis:   mainApp,incoming,+37112345678,209,SIP/,+37112345678,incoming-queueLogistic
        socketio.emit(event['type'], event)
        #channel.answer()

        myEndpoint = args[1].replace('+', '')
        number = args[2]
        trunk = args[3]
        cid = args[4]
        dcontext = args[5]

        Allcl[channel_id] = AutoClient(channel_id, myEndpoint, cid, '000')
        cl = Allcl[channel_id]

        with open(AppPath + '/data/'+dcontext, 'r') as f:
            queueFile = f.read()
        cl.queueList = queueFile.split('\n')
        cl.trunk = trunk
        cl.incoming = True
        cl.timeout = 10
        cl.autoCall = False
        cl.dcontext = dcontext
        cl.pinCodeAccepted = True
        cl.ip = '192.168.2.3'
        cl.channel0_ID = channel_id
        cl.channel0 = channel
        cl.channel0.answer()
        cl.channel0.startMoh()
        cl.MOHenabled = True
        cl.connected = True
        cl.bridge = ARIclient.bridges.create(
            type='mixing', bridgeId=cl.bridgeID)
        handle_calltoclient(myEndpoint, cl.queueList[0], trunk, cid, 'false')

    if args[0] == 'outgoing':
        myEndpoint = args[1]
        number = args[2]
        trunk = args[3]
        cid = args[4]
        dcontext = args[5]

        Allcl[channel_id] = AutoClient(channel_id, myEndpoint, cid, '000')
        cl = Allcl[channel_id]
        cl.autoCall = False
        cl.dcontext = dcontext
        cl.pinCodeAccepted = True
        cl.ip = '192.168.2.3'
        cl.channel0_ID = channel_id
        cl.MOHenabled = False
        cl.channel0 = channel
        print channel_id, cl.ip
        cl.connected = True
        #cl.channel0.answer()
        cl.channel0.ring()
        cl.bridge = ARIclient.bridges.create(
            type='mixing', bridgeId=cl.bridgeID)
        print myEndpoint, number, trunk, cid, 'true'
        handle_calltoclient(myEndpoint, number, trunk, cid, 'false')


def stasis_end(channel_obj, event):
    channel = event.get('channel')
    chan_id = channel.get('id')
    print "stasis_end__________________________________________", chan_id
    clientSid = GetClientByChanIDEvent(channel_obj, event)
    if clientSid == None:
        return

    cl = Allcl[clientSid]
    BroadcastEvent(cl.endpoint, event)

    #if cl.incoming and cl.bridgeUP: SafeHangup(cl.channel0_ID)
    #if not cl.incoming: SafeHangup(cl.channel0_ID)

    SafeHangup(cl.channel0_ID)
    SafeHangup(cl.channel1_ID)
    if cl.incoming:
        cl.queueStop = True

    if cl.channel1_wrongDest:
        cl.end = datetime.datetime.now()
        cl.channel1_disposition = "Network out of order"  # cl.channel0_disposition""
        print "stasis_end______________WriteCallToDb"
        runner3 = pool.spawn_n(WriteCallToDb, cl)
        runner4 = pool.spawn_n(RemoveClient,cl.sid)

###########################################################

def RemoveClient(clientSid):
    global Allcl_lock
    while (Allcl_lock==True): eventlet.sleep(1)
    Allcl_lock = True
    try:
        del Allcl[clientSid]
    except:
        log.info('RemoveClient__________error, clientSid:%s',clientSid)
    log.info('RemoveClient__________Allcl size = :%s',str(len(Allcl)))
    Allcl_lock = False
def BroadcastEvent(ep, event):
    try:
        for request_sid, endpoint_list in EndpointEvents.iteritems():
            for endpoint in endpoint_list:
                if endpoint == ep:
                    socketio.emit(event['type'], event, room=request_sid)
    except:
        print 'BroadcastEvent_________________________________error'


def Queue(cl):
    if cl.queueStop:
        SafeHangup(cl.channel0_ID)
        SafeHangup(cl.channel1_ID)
        #del Allcl[cl.sid]
        runner4 = pool.spawn_n(RemoveClient,cl.sid)
    else:
        if cl.queueClientUnavailable: eventlet.sleep(1)
        else: eventlet.sleep(10)
        cl.queueClientUnavailable = False
        
        #   channel = channel.continueInDialplan( context=context, extension=extension, priority=priority)
        #cl.channel0.continueInDialplan()
        cl.queueListCntr += 1
        if cl.queueListCntr == len(cl.queueList):
            cl.queueListCntr = 0
        handle_calltoclient(
            cl.endpoint, cl.queueList[cl.queueListCntr], cl.trunk, cl.cid, 'false')


def ContinueInDialplan(cl):
    eventlet.sleep(5)
    #   channel = channel.continueInDialplan( context=context, extension=extension, priority=priority)
    cl.channel0.continueInDialplan()


def Play_tone(cl, clientSid):

    # Invalid number format
    # Call Rejected
    # Normal Clearing
    # User busy
    # Unallocated (unassigned) number
    # User alerting, no answer

    try:  # ringing, stutter, busy, congestion, CHANUNAVAIL
        if 'Normal Clearing' in cl.channel1_disposition:
            cl.channel0.play(media='tone:busy;tonezone=us')
        if 'User busy' in cl.channel1_disposition:
            cl.channel0.play(media='tone:busy;tonezone=us')
        if 'User alerting, no answer' in cl.channel1_disposition:
            cl.channel0.play(media='tone:busy;tonezone=us')
        if 'Unallocated (unassigned) number' in cl.channel1_disposition:
            cl.channel0.play(media='tone:congestion;tonezone=us')
        if 'Invalid number format' in cl.channel1_disposition:
            cl.channel0.play(media='tone:congestion;tonezone=us')
        if 'Call Rejected' in cl.channel1_disposition:
            cl.channel0.play(media='tone:congestion;tonezone=us')
        if 'Network out of order' in cl.channel1_disposition:
            cl.channel0.play(media='tone:congestion;tonezone=us')

    except:
        print "Play_tone  _________________error ", clientSid

    eventlet.sleep(2)
    try:
        SafeHangup(cl.channel0_ID)
        #del Allcl[clientSid]
        runner4 = pool.spawn_n(RemoveClient,cl.sid)
    except:
        print "Play_tone  _________________error SafeHangup ", clientSid


def WriteCallToDb(cl):

    global TotallCalls
    global DB_lock
    
    while (DB_lock==True): eventlet.sleep(1)
    DB_lock = True

    try:
        queryNumber = cl.endpoint
        if cl.incoming:
            queryNumber = cl.dst

        userid = 'NULL'
        userfield = 'NULL'
    
        kAdmin = siptoid.KeycloakAdm()
        kcID = kAdmin.GetIdBySip(queryNumber)
        if kcID is not None:
            userid = kcID
        kcUsername = kAdmin.GetNameBySip(queryNumber)
        if kcUsername is not None: 
            userfield = kcUsername

     
        TotallCalls = TotallCalls+1
        calldate = cl.start
        clid = '"' + cl.endpoint + '" <' + cl.cid + '>'
        src = cl.cid
        dst = cl.dst
        dcontext = cl.dcontext  # 'from-'+ariName
        channel = cl.channel0_ID
        dstchannel = cl.channel1_ID
        lastapp = 'Dial'
        lastdata = cl.lastData
        duration = (cl.end - cl.start).total_seconds()
        disposition_ext = cl.channel1_disposition
        if cl.bridgeUP:
            disposition = 'ANSWERED'
            answer = cl.answer
            billsec = (cl.end - cl.answer).total_seconds()
            recURL = 'http://media.mydomain:88/autoDial' + cl.recPath + cl.mediaExt
        else:
            # answer = 0
            disposition = 'NO ANSWER'
            if disposition_ext in disposition_convert:
                disposition = disposition_convert[disposition_ext]
            billsec = '0'
            recURL = 'NULL'
        start = cl.start
        end = cl.end

        amaflags = '3'

        if cl.incoming:
            accountcode = 'NULL'
            peeraccount = cl.dst
        else:
            accountcode = cl.endpoint
            peeraccount = 'NULL'

        
        uniqueid = cl.uniqueid

        linkedid = ''
        sequence = str(TotallCalls)
        provider = ''
        price = '0.0'

        insert_queryStart = 'INSERT INTO '
        insert_queryEnd = ''

        if cl.bridgeUP:
            insert_queryEnd = """ (
        calldate,
        clid,
        src,
        dst,
        dcontext,
        channel,
        dstchannel,
        lastapp,
        lastdata,
        duration,
        billsec,
        start,
        answer,
        end,
        disposition,
        disposition_ext,
        amaflags,
        accountcode,
        userfield,
        userid,
        uniqueid,
        peeraccount,
        linkedid,
        sequence,
        recURL,
        provider,
        price) VALUES ('%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s')""" % (
                calldate,
                clid,
                src,
                dst,
                dcontext,
                channel,
                dstchannel,
                lastapp,
                lastdata,
                duration,
                billsec,
                start,
                answer,
                end,
                disposition,
                disposition_ext,
                amaflags,
                accountcode,
                userfield,
                userid,
                uniqueid,
                peeraccount,
                linkedid,
                sequence,
                recURL,
                provider,
                price)
        else:
            insert_queryEnd = """ (
            calldate,
            clid,
            src,
            dst,
            dcontext,
            channel,
            dstchannel,
            lastapp,
            lastdata,
            duration,
            billsec,
            start,
            end,
            disposition,
            disposition_ext,
            amaflags,
            accountcode,
            userfield,
            userid,
            uniqueid,
            peeraccount,
            linkedid,
            sequence,
            recURL,
            provider,
            price) VALUES ('%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s','%s')""" % (
                calldate,
                clid,
                src,
                dst,
                dcontext,
                channel,
                dstchannel,
                lastapp,
                lastdata,
                duration,
                billsec,
                start,
                end,
                disposition,
                disposition_ext,
                amaflags,
                accountcode,
                userfield,
                userid,
                uniqueid,
                peeraccount,
                linkedid,
                sequence,
                recURL,
                provider,
                price)

        insert_queryCDR = insert_queryStart + "cdr" + insert_queryEnd
        
        cnxProd = mysql.connector.connect(
            user='admin_nba', password='xxxxxxxx', host='apm.mydomain', database='admin_nba')
        cursorProd = cnxProd.cursor(buffered=True)
        cursorProd.execute(insert_queryCDR)
        cursorProd.execute('COMMIT')
        cursorProd.close()
        cnxProd.close()
        log.info('WriteCallToDb________apm.mydomain: %s', insert_queryCDR)

        cnxLocal = mysql.connector.connect(user='admin_nba', password='xxxxxxxxx', host='0.0.0.0', database='admin_nba')
        cursorLocal = cnxLocal.cursor(buffered=True)
        cursorLocal.execute(insert_queryCDR)
        cursorLocal.execute('COMMIT')
        cursorLocal.close()
        cnxLocal.close()

    except:
        log.info('WriteCallToDb________error: %s,%s',cl.endpoint,cl.dst)
    DB_lock = False


def CheckPINcode(cl, channel, digit):
    if cl.pinCodeAccepted:
        return
    if cl.pinCodeTMP == cl.pinCode:
        cl.channel0 = channel
        cl.channel0.play(media='sound:pls-wait-connect-call')
        cl.pinCodeTMP = ''
        cl.pinCodeAccepted = True
        socketio.emit('pinCodeAccepted', cl.pinCodeAccepted, room=cl.sid)
        log.info('pinCodeAccepted %s, %s', cl.sid, cl.ip)
        cl.connected = True
        cl.channel0.startMoh()
        cl.MOHenabled = True
        cl.bridge = ARIclient.bridges.create(
            type='mixing', bridgeId=cl.bridgeID)

    else:
        cl.channel0.play(media='digits:' + digit)
        cl.pinCodeTMP += digit

    if len(cl.pinCodeTMP) > len(cl.pinCode):
        # channel.play(media='sound:vm-goodbye')
        socketio.emit('pinCodeAccepted', cl.pinCodeAccepted, room=cl.sid)
        log.info('Not pinCodeAccepted %s, %s', cl.sid, cl.ip)
        cl.channel0.play(media='sound:vm-invalidpassword')

        eventlet.sleep(7)
        try:
            cl.channel0.hangup()
        except:
            pass


def GetClientByChanID(channel, event):
    chanID = channel.json.get('id')
    # print chanID
    # print len(Allcl)
    for sid, cl in Allcl.iteritems():
        # print cl.channel0_ID
        if chanID == cl.channel0_ID or chanID == cl.channel1_ID:
            return cl.sid
    return None


def GetClientByChanIDEvent(channel, event):
    channel = event.get('channel')
    chanID = channel.get('id')

    # print chanID
    # print len(Allcl)
    for sid, cl in Allcl.iteritems():
        # print cl.channel0_ID
        if chanID == cl.channel0_ID or chanID == cl.channel1_ID:
            return cl.sid
    return None


def GetClientByBridgeID(bridge, event):
    bridgeID = bridge.json.get('id')
    for sid, cl in Allcl.iteritems():
        if bridgeID == cl.bridgeID:
            return cl.sid
    return None


def SafeHangup(channelID):
    """Safely hang up the specified channel"""
    try:
        ARIclient.channels.hangup(channelId=channelID)
    except:
        return


def SafeBridgeDestroy(bridge):
    """Safely destroy the specified bridge"""
    #try:
    #    bridge.destroy()
    #except requests.HTTPError as e:
    #    if e.response.status_code != requests.codes.not_found:
    #        print e.response.status_code
    try:
        bridge.destroy()
    except:
        return


def StartStopMOH(sid):
    cl = Allcl[sid]
    if cl.MOHenabled:
        cl.channel0.startMoh()
    else:
        cl.channel0.stopMoh()


def RegisterARIevents():
    # ARIclient.on_event('Dialed', Dialed)
    # ARIclient.on_channel_event('ChannelVarset', ChannelVarset)
    # ARIclient.on_channel_event('ChannelHangupRequest', ChannelHangupRequest)
    # ARIclient.on_channel_event('ChannelCreated', ChannelCreated)
    # ARIclient.on_channel_event('ChannelTalkingStarted', ChannelTalkingStarted)
    # ARIclient.on_channel_event('ChannelTalkingFinished', ChannelTalkingFinished)
    #ARIclient.on_channel_event('ChannelDialplan', ChannelDialplan)
    ARIclient.on_channel_event('StasisStart', stasis_start)
    ARIclient.on_channel_event('StasisEnd', stasis_end)
    ARIclient.on_channel_event('ChannelStateChange', ChannelStateChange)
    ARIclient.on_channel_event('ChannelDestroyed', ChannelDestroyed)
    ARIclient.on_channel_event('ChannelDtmfReceived', ChannelDtmfReceived)
    ARIclient.on_bridge_event('ChannelLeftBridge', ChannelLeftBridge)
    ARIclient.on_bridge_event('ChannelEnteredBridge', ChannelEnteredBridge)


def ARIrunProd():
    while(True):
        try:
            RegisterARIevents()
            ARIclient.run(apps=ariName)
        except socket.error as e:
            print e
            if e.errno == 32:  # Broken pipe
                pass
        except ValueError as e:
            print e.message
            #if e.message == 'No JSON object could be decoded':  # client.close()


def ARIrunDebug():

    RegisterARIevents()
    ARIclient.run(apps=ariName)
    print 'ARIclient closed'


#########################################################
# Flask socketio
#######################################################


#   js :    socket.emit('EndpointEvents', strEndpoint);
#   js :    socket.emit('EndpointEventsRemove', strEndpoint);
@socketio.on('EndpointEvents')
def handle_EndpointEvents(endpoint):
    print endpoint
    if not request.sid in EndpointEvents:
        EndpointEvents[request.sid] = [endpoint, ]
    else:
        if not endpoint in EndpointEvents[request.sid]:
            EndpointEvents[request.sid].append(endpoint)
    print 'handle_EndpointEvents___________________________________________________________________________________', EndpointEvents[request.sid]
#   js :  socket.emit('EndpointEventsRemove', strEndpoint);
@socketio.on('EndpointEventsRemove')
def handle_EndpointEventsRemove(endpoint):
    for request_sid, endpoint_list in EndpointEvents.iteritems():
        for ep in endpoint_list:
            if endpoint == ep:
                endpoint_list.remove(ip)


@socketio.on_error_default  # handles all namespaces without an explicit error handler
def default_error_handler(e):
    print e.message
    


@socketio.on('endpointDown')
def handle_endpointDown(myEndpoint):
    cl = Allcl[request.sid]
    SafeHangup(cl.channel0_ID)
    SafeHangup(cl.channel1_ID)
    if cl.pinCodeAccepted:  # aditional total clear
        for channel in ARIclient.channels.list():
            chanId = channel.json.get('id')
            if chanId == cl.channel0_ID:
                try:
                    channel.hangup()
                except:
                    j = 0
            if len(cl.channel0_ID) > 3:
                if chanId.startswith('55555.' + cl.endpoint):
                    try:
                        channel.hangup()
                    except:
                        j = 0
    #del Allcl[request.sid]
    runner4 = pool.spawn_n(RemoveClient,request.sid)


@socketio.on('MOH')
def handle_MOH(myEndpoint):
    Allcl[request.sid].MOHenabled = not Allcl[request.sid].MOHenabled
    StartStopMOH(request.sid)


@socketio.on('hangupClient')
def handle_hangupClient(myEndpoint):
    SafeHangup(Allcl[request.sid].channel1_ID)
    # print 'hangupClient'
    # for channel in ARIclient.channels.list():
    #     chanId = channel.json.get('id')
    #     for sid, cl in Allcl.iteritems():
    #         if chanId == cl.channel1_ID:
    #             channel.hangup()


@socketio.on('endpointUP')
def handle_endpointUP(myEndpoint, trunk, cid, pinCode):
    print 'endpointUP', myEndpoint, trunk, cid, pinCode
    Allcl[request.sid] = AutoClient(request.sid, myEndpoint, cid, pinCode)
    cl = Allcl[request.sid]
    cl.ip = request.remote_addr
    SafeHangup(cl.channel0_ID)
    try:
        cl.channel0 = ARIclient.channels.originate(endpoint=trunk + myEndpoint,
                                                   callerId=cid,
                                                   timeout=33,
                                                   app=ariName,
                                                   appArgs='dialed',
                                                   channelId=cl.channel0_ID)
    except requests.HTTPError as e:
        socketio.emit('endpointUP_error',
                      e.response.status_code, room=request.sid)
        log.info('endpointUP_error myEndpoint = %s, trunk = %s, e.response.status_code = %s', myEndpoint, trunk,
                 e.response.status_code)
    # cl.channel0 = ARIclient.channels.create(endpoint=trunk + myEndpoint,
    #                                                       app='autoDial',
    #                                                       channelId=cl.channel0_ID)


@socketio.on('calltoclient')
def handle_calltoclient(myEndpoint, number, trunk, cid, beep):
    for sid, val in Allcl.iteritems():
        if myEndpoint == val.endpoint:
            cl = Allcl[sid]
            if not cl.pinCodeAccepted:  # close client
                socketio.emit(
                    'alertMSG', 'Your PIN is not accepted!', room=request.sid)
                handle_endpointDown(myEndpoint)
                return
            cl.channel1_ID = '55555.' + myEndpoint + '.' + number
            cl.cid = cid
            cl.dst = number
            cl.start = datetime.datetime.now()
            cl.answer = None
            cl.end = None
            cl.bridgeUP = False
            cl.channel0_disposition = None
            cl.channel1_disposition = None
            cl.lastData = trunk + number
            cl.uniqueid = time.time()
            cl.beep = beep

            # ARIclient.channels.setChannelVar(channelId=cl.channel0_ID, variable="CALLERID(num)", value=cl.cid)

            # channel = ARIclient.channels.originate(endpoint=trunk + number,
            #                                        callerId=cid,
            #                                        timeout=33,
            #                                        app=ariName,
            #                                        channelId=Allcl[sid].channel1_ID,
            #                                        #context='from-autodial',
            #                                        appArgs='local',
            #                                        originator = Allcl[sid].channel0_ID)
            try:
                cl.channel1 = ARIclient.channels.originate(endpoint=trunk + number,
                                                           callerId=cid,
                                                           timeout=cl.timeout,
                                                           app=ariName,
                                                           appArgs='dialed',
                                                           channelId=cl.channel1_ID,
                                                           originator=cl.channel0_ID)
                log.info('calltoclient %s %s %s %s %s',
                         myEndpoint, number, trunk, cid, beep)
                time.sleep(0.05)
            #except HTTPError as e:
            except:
                log.info('handle_calltoclient________except___cl.incoming=%s, cl.queueClientUnavailable=%s, %s, %s', cl.incoming,cl.queueClientUnavailable,myEndpoint, number)
                if cl.incoming:
                    # if calling channel alive - continue queue
                    cl.queueClientUnavailable=True
                    pool.spawn_n(Queue, cl)
                else:
                    cl.channel1_wrongDest = True


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def get_resource(path):  # pragma: no cover
    ipOk = False
    for ip in IpWhiteList:
        if str(request.remote_addr).startswith(ip.strip()):
            ipOk = True
        if str(request.remote_addr) == ip.strip():
            ipOk = True

    if not ipOk:
        return abort(403)
    if len(path) > 80:
        return abort(413)

    mimetypes = {
        ".css": "text/css",
        ".html": "text/html",
        ".js": "application/javascript",
    }
    complete_path = os.path.join(AppPath, path)
    ext = os.path.splitext(path)[1]

    mimetype = mimetypes.get(ext, 'None')
    if mimetype == 'None':
        return abort(404)
    if not os.path.exists(complete_path) or len(ext) == 0:
        return abort(403)

    with open(complete_path) as f:
        content = f.read()
    return Response(content, mimetype=mimetype)


@socketio.on('disconnect')
def handle_disconnect():
    SafeHangup(Allcl[request.sid].channel0_ID)
    SafeHangup(Allcl[request.sid].channel1_ID)
    log.info('socketio.on disconnect %s, %s',
             Allcl[request.sid].sid, Allcl[request.sid].ip)
    #del Allcl[request.sid]
    runner4 = pool.spawn_n(RemoveClient,request.sid)
    if request.sid in EndpointEvents:
        del EndpointEvents[request.sid]


@socketio.on('connect')
def handle_connect():
    # print 'connect_handler, remote ip= ', request.remote_addr
    ipOk = False
    for ip in IpWhiteList:
        if str(request.remote_addr).startswith(ip.strip()):
            ipOk = True
        if str(request.remote_addr) == ip.strip():
            ipOk = True
    if not ipOk:
        return False

    # print request.remote_addr


def Initialize():
    with open(AppPath + '/whitelist.conf') as f:
        arr = f.read().splitlines()
        for ip in arr:
            if not ip.strip().startswith('#'):
                if not (ip.isupper() or ip.islower()):
                    if len(ip) > 3:
                        log.info('whitelist.conf= %s', ip)
                        IpWhiteList.append(ip)



ariName = 'mainApp'

if __name__ == '__main__':

    Initialize()
    runner = pool.spawn_n(ARIrunProd)
    #runner = pool.spawn_n(ARIrunDebug)

    host = '0.0.0.0'
    port = 5571
    print host, port, ariName
    socketio.run(app, host=host, port=port,
                 debug=True,
                 certfile='/etc/letsencrypt/live/nba.mydomain/fullchain.pem',
                 keyfile='/etc/letsencrypt/live/nba.mydomain/privkey.pem')
