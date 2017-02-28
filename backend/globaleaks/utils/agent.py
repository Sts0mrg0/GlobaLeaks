# -*- coding: utf-8 -*-

from twisted.internet import defer, reactor
from twisted.internet.protocol import Protocol
from twisted.web.client import Agent, readBody

try:
    from twisted.web.client import URI
except ImportError:
    from twisted.web.client import _URI as URI


class SaveContents(Protocol):
    def __init__(self, finished, filesize, filename):
        self.finished = finished
        self.remaining = filesize
        self.outfile = open(filename, 'wb')

    def dataReceived(self, bytes):
        if self.remaining:
            display = bytes[:self.remaining]
            self.outfile.write(display)
            self.remaining -= len(display)

    def connectionLost(self, reason):
        self.outfile.close()
        self.finished.callback(None)


def getPageSecurely(url):
    request = Agent(reactor, connectTimeout=4).request('GET', url)

    def cbResponse(response):
        return readBody(response)

    request.addCallback(cbResponse)

    return request


def downloadPageSecurely(url, filename):
    request = Agent(reactor, connectTimeout=4).request('GET', url)
    finished = defer.Deferred()

    def cbResponse(response):
        response.deliverBody(SaveContents(finished, response.length, filename))

    request.addCallback(cbResponse)

    return finished