from twisted.application import service
from twisted.internet.task import LoopingCall
import os, sys, time, atexit

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(SCRIPT_DIR, 'txlb'))

from twisted.internet import defer, reactor
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Factory, Protocol, ClientFactory,\
        ServerFactory, DatagramProtocol
from twisted.internet.endpoints import TCP4ClientEndpoint
from twisted.protocols.basic import NetstringReceiver
from twisted.python import log

from txlb import manager, config
from txlb.model import HostMapper
from txlb.schedulers import roundr, leastc
from txlb.application.service import LoadBalancedService
from txlb.manager import checker

typ = roundr

proxyServices = [
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host0',
      address='127.0.0.1:10000'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host1',
      address='127.0.0.1:10001'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host2',
      address='127.0.0.1:10002'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host3',
      address='127.0.0.1:10003'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host4',
      address='127.0.0.1:10004'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host5',
      address='127.0.0.1:10005'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host6',
      address='127.0.0.1:10006'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host7',
      address='127.0.0.1:10007'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host8',
      address='127.0.0.1:10008'),
  HostMapper(proxy='127.0.0.1:8080', lbType=typ, host='host9',
      address='127.0.0.1:10009'),
]

# Amazon AWS commands
class AmazonAWS(object):
    def __init__(self):
        self.conn = boto.ec2.connect_to_region("us-west-2")

    def start_worker(self):
        return self.conn.run_instances("ami-64ad3554", key_name='herik#cburkhal', instance_type='t1.micro')

    def term_worker(self, worker):
        return self.conn.terminate_instances(instance_ids=[w.id for w in worker.instances])

# overlay commands
class LoadBalanceService(service.Service):

    def __init__(self, tracker):
        self.tracker = tracker
        from twisted.internet.task import LoopingCall
        LoopingCall(self.reccuring).start(3)

    def startService(self):
        service.Service.startService(self)

    def reccuring(self):
        #print self.tracker.getStats()
        pass

# Overlay Communication
class OverlayService(object):
    overlay = None

    def OK(self, reply):
        pass
        
    def JoinReceived(self, reply):
        print "JoinReceived"
        return
        

    commands = {"ok" : OK,
                "join_accept" : JoinReceived }

class ClientProtocol(NetstringReceiver):
    def connectionMade(self):
        self.sendRequest(self.factory.request)

    def sendRequest(self, request):
        print request
        self.sendString(json.dumps(request))

    def stringReceived(self, reply):
        print reply
        self.transport.loseConnection()
        reply = json.loads(reply)
        command = reply["command"]

        if command not in self.factory.service.commands:
            print "Command <%s> does not exist!" % command
            self.transport.loseConnection()
            return

        self.factory.handeReply(command, reply)

class ServerProtocol(NetstringReceiver):
    def stringReceived(self, request):
        command = json.loads(request)["command"]
        data = json.loads(request)

        if command not in self.factory.service.commands:
            print "Command <%s> does not exist!" % command
            self.transport.loseConnection()
            return

        self.commandReceived(command, data)

    def commandReceived(self, command, data):
        reply = self.factory.reply(command, data)

        if reply is not None:
            self.sendString(json.dumps(reply))

        self.transport.loseConnection()
        
class NodeClientFactory(ClientFactory):

    protocol = ClientProtocol

    def __init__(self, service, request):
        self.request = request
        self.service = service
        self.deferred = defer.Deferred()

    def handleReply(self, command, reply):
        def handler(reply):
            return self.service.commands[command](self.service, reply)
        cmd_handler = self.service.commands[command]
        if cmd_handler is None:
            return None
        self.deferred.addCallback(handler)
        self.deferred.callback(reply)

    def clientConnectionFailed(self, connector, reason):
        if self.deferred is not None:
            d, self.deferred = self.deferred, None
            d.errback(reason)

class NodeServerFactory(ServerFactory):

    protocol = ServerProtocol

    def __init__(self, service):
        self.service = service

    def reply(self, command, data):
        create_reply = self.service.commands[command]
        if create_reply is None: # no such command
            return None
        try:
            return create_reply(self.service, data)
        except:
            traceback.print_exc()
            return None # command failed
        


# initialization
class Overlay():
    is_coordinator = False
    coordinator = None
    members = []
    
    def join(self):
        print "start join"
        def send(_, node):
            class ReturnValue():
                def __init__(self):
                    self.success = False
                def callback(self):
                    self.success = True
                    
            result = ReturnValue()
        
            print "tcp before"
            factory = NodeClientFactory(OverlayService(), {"command" : "join"})
            reactor.connectTCP(monitor["host"], monitor["tcp_port"], factory)
            print "tcp finished"
            #factory.deferred.addCallback(result.callback)
            #if not result.success:
            #    print "raise exception"
            #    raise
            return factory.deferred
        def success(_,node):
            print "success"
            coordinator = node
        def error(_):
            log.err("ERROR")
            is_coordinator = True
            print "I am coordinator"
        # search for running loadbalancers and join the overlay network
        nodes = self.read_config()
        initialized = False
        d = Deferred()
        print nodes
        for node in nodes:
            print "add node" + str(node)
            d.addErrback(send, node)
        d.addCallbacks(success, error)
        
        d.errback(0)           

    def read_config(self):
        # read loadbalancer ip's
        f = open("load_balancers.txt", "r")
        nodes = []
        for line in f:
            s = line.split(":")
            nodes.append({"ip":s[0],"port":int(s[1].strip())})
        return nodes

def init():
    pass

# cleanup and exit
def before_exit():
    sys.exit(0)
    
o = Overlay()
o.join()
print "start overlay"

application = service.Application('Demo LB Service')
pm = manager.proxyManagerFactory(proxyServices)
lbs = LoadBalancedService(pm)
configuration = config.Config("config.xml")
print pm.trackers
os = OverlayService(pm.getTracker('proxy1', 'group1'))
os.setServiceParent(application)
lbs.setServiceParent(application)

atexit.register(before_exit)
