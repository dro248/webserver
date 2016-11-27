import errno
import select
import socket
import sys
import traceback
import argparse
import logging
from httplib import HTTPResponse

class Poller:
    """ Polling server """
    def __init__(self,args):
        self.host = ""
        self.port = args.port
        self.open_socket()
        self.clients = {}
        self.cache = {}
        self.size = 1024 * 10
        logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARN)

    def open_socket(self):
        """ Setup the socket for incoming clients """
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
            self.server.bind((self.host,self.port))
            self.server.listen(5)
            self.server.setblocking(0)
        except socket.error, (value,message):
            if self.server:
                self.server.close()
            print "Could not open socket: " + message
            sys.exit(1)

    def run(self):
        """ Use poll() to handle each incoming client."""
        self.poller = select.epoll()
        self.pollmask = select.EPOLLIN | select.EPOLLHUP | select.EPOLLERR
        self.poller.register(self.server,self.pollmask)
        while True:
            # poll sockets
            try:
                fds = self.poller.poll(timeout=1)
            except:
                return
            for (fd,event) in fds:
                # handle errors
                if event & (select.POLLHUP | select.POLLERR):
                    self.handleError(fd)
                    continue
                # handle the server socket
                if fd == self.server.fileno():
                    self.handleServer()
                    continue
                # handle client socket
                result = self.handleClient(fd)

    def handleError(self,fd):
        self.poller.unregister(fd)
        if fd == self.server.fileno():
            # recreate server socket
            self.server.close()
            self.open_socket()
            self.poller.register(self.server,self.pollmask)
        else:
            # close the socket
            self.clients[fd].close()
            del self.cache[fd]
            del self.clients[fd]

    def handleServer(self):
        # accept as many clients as possible
        while True:
            try:
                (client,address) = self.server.accept()
            except socket.error, (value,message):
                # if socket blocks because no clients are available,
                # then return
                if value == errno.EAGAIN or errno.EWOULDBLOCK:
                    return
                print traceback.format_exc()
                sys.exit()
            # set client socket to be non blocking
            client.setblocking(0)
            self.clients[client.fileno()] = client
            self.cache[client.fileno()] = ""
            self.poller.register(client.fileno(),self.pollmask)

    def reappend(string, delim):
        return string+delim

    def handleClient(self,fd):
        try:
            data = self.clients[fd].recv(self.size)
        except socket.error, (value,message):
            # if no data is available, move on to another client
            if value == errno.EAGAIN or errno.EWOULDBLOCK:
                return
            print traceback.format_exc()
            sys.exit()

        if data:
            # TODO:
            #   - if there is data received on this socket, handle it
            #       - if it isn't a full message, store it in the client's cache
            #       - otherwise, parse it with HTTPParser
            # if end of http request found...
            print data
            if data.find("\r\n\r\n") != -1:
                chunks = data.split("\r\n\r\n")

                # adding the \r\n\r\n back into the chunks that had it
                for i in range(len(chunks)):
                    if i is not range(len(chunks)-1):
                        logging.debug("adding \\r\\n\\r\\n to %s" % chunks[i])
                        chunks[i] += "\r\n\r\n"

                # append the last bit of the request to the cache
                self.cache[fd] += chunks[0]
                self.handle_request(self.cache[fd])
                # remove the last bit of the request that we are handling and clear the cache
                chunks.pop(0)
                self.cache[fd] = ""

                for req in chunks:
                    if req.endswith("\r\n\r\n"):
                        #return a response...and stuff 
                        self.handle_request(req)
                    else:
                        self.cache[fd] = req
            else:
                # append stuff to cache
                self.cache[fd] += data

        else:
            self.poller.unregister(fd)
            self.clients[fd].close()
            del self.cache[fd]
            del self.clients[fd]

    def parse_request(self, req):
        resp = HTTPResponse(req)
        return resp.begin()

    def handle_request(self, request):
        head = self.parse_request(request)
        logging.debug(head.)
        # Form the response
        self.clients[fd].send("okay\n")

