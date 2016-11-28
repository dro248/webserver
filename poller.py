import errno
import select
import socket
import sys
import traceback
import argparse
import logging
import os
from urlparse import urlparse
from wsgiref.handlers import format_date_time
from datetime import datetime
from time import mktime

try:
    from http_parser.parser import HttpParser
except ImportError:
    from http_parser.pyparser import HttpParser

class Poller:
    """ Polling server """
    def __init__(self,args):
        logging.debug("Poller.__init__()...")
        logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARN)
        
        # parse web.conf
        configs = self.parse_conf_file()

        # set server cnnfiguration from web.conf
        self.host = self.get_host(configs)
        self.root = self.get_root(configs)
        self.port = args.port
        self.open_socket()
        self.supportedMIMEtypes = self.get_supportedMIMEtypes(configs)
        self.timeout = self.get_timeout(configs)
        self.clients = {}
        self.clientIdleTime = {}
        self.cache = {}
        self.size = 1024 * 10

        logging.debug("CONFIGS: %s" % configs)
        logging.debug("Host: %s" % self.host)
        logging.debug("Root: %s" % self.root)
        logging.debug("Supported MIME types: %s" % self.supportedMIMEtypes)
        logging.debug("timeout: %s" % self.timeout)

        ##############################################

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
            logging.error("Could not open socket: " + message + "\nExiting. Gracefully.\n=)")
            sys.exit(1)

    def run(self):
        """ Use poll() to handle each incoming client."""
        self.poller = select.epoll()
        self.pollmask = select.EPOLLIN | select.EPOLLHUP | select.EPOLLERR
        self.poller.register(self.server,self.pollmask)
        while True:
            # poll sockets
            try:
                # poll sockets every half second
                fds = self.poller.poll(timeout=0.5)

                # update idle time for each client
                for client in self.clientIdleTime:
                    self.clientIdleTime[client] += 0.5
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

            ###############################################
            #             SWEEP -- (MARK & SWEEP)         #
            # TODO: Kick off clients idle for max timeout #
            ###############################################
            # print "clientsIdle...",self.clientIdleTime
            # print "clients:", self.clients

            fdsToBeDeleted = []
            for client_fd in self.clientIdleTime:
                # print "client_fd::::", client_fd
                if self.clientIdleTime[client_fd] >= self.timeout:
                    self.poller.unregister(client_fd)
                    try:
                        self.clients[client_fd].close()
                    except:
                        pass
                    try:
                        del self.cache[client_fd]
                    except:
                        pass
                    try:
                        del self.clients[client_fd]
                    except:
                        pass
                    fdsToBeDeleted.append(client_fd)

            # Delete client time-entries for clients that have been deleted
            for fd in fdsToBeDeleted:
                try:
                    del self.clientIdleTime[fd]
                except:
                    pass


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
            # Delete client timestamp on client deletion
            del self.clientIdleTime[fd]

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
                logging.error(traceback.format_exc())
                sys.exit()
            # set client socket to be non blocking
            client.setblocking(0)
            self.clients[client.fileno()] = client

            # Create client timestamp on client creation
            self.clientIdleTime[client.fileno()] = 0

            self.cache[client.fileno()] = ""
            self.poller.register(client.fileno(),self.pollmask)

    def handleClient(self,fd):
        try:
            data = self.clients[fd].recv(self.size)
        except socket.error, (value,message):
            # if no data is available, move on to another client
            if value == errno.EAGAIN or errno.EWOULDBLOCK:
                return
            logging.error(traceback.format_exc())
            sys.exit()

        if data:
            # if end of http request found...
            if "\r\n\r\n" in data:
                chunks = data.split("\r\n\r\n")

                # adding the \r\n\r\n back into the chunks that had it
                for i in range(len(chunks)):
                    if i is not len(chunks)-1:
                        #logging.debug("adding \\r\\n\\r\\n to %s" % chunks[i])
                        chunks[i] += "\r\n\r\n"

                # append the last bit of the request to the cache
                self.cache[fd] += chunks[0]
                self.handle_request(self.cache[fd], fd)
                # remove the last bit of the request that we are handling and clear the cache
                chunks.pop(0)
                self.cache[fd] = ""

                for req in chunks:
                    if req.endswith("\r\n\r\n"):
                        self.handle_request(req, fd)
                    else:
                        self.cache[fd] = req
            else:
                logging.debug("Appending to cache[%i] += %s" % (fd, data))
                self.cache[fd] += data
                if "\r\n\r\n" in self.cache[fd]:
                    #TODO: check for multiple requests in cache
                    request_end_index = self.cache[fd].find("\r\n\r\n") + 4
                    request = self.cache[fd][:request_end_index]
                    self.handle_request(request, fd)
                    self.cache[fd] = self.cache[fd][request_end_index:]
        else:
            # when does this happen?
            self.poller.unregister(fd)
            self.clients[fd].close()
            del self.cache[fd]
            del self.clients[fd]
            # Delete client timestamp on client deletion
            del self.clientIdleTime[fd]


        ##############################################
        #           MARK -- (MARK & SWEEP)           #
        # TODO: reset time to 0 for specified client #
        ##############################################
        self.clientIdleTime[fd] = 0



    def parse_request(self, req):
        parser = HttpParser()
        num_parsed = parser.execute(req, len(req))
        return parser

    def rfc_1123_date(self, timestamp=0):
        now = datetime.fromtimestamp(timestamp) if timestamp != 0 else datetime.now()
        stamp = mktime(now.timetuple())
        return format_date_time(stamp)

    def get_filename(self, path):
        basename, ext = os.path.splitext(path)
        if basename is "/":
            basename = "/index"
            ext = ".html"
        logging.debug("docRoot: %s, path: %s, basename: %s, ext: %s" % (self.root, path, basename, ext))
        return (self.root + basename + ext, basename, ext)

    def get_file(self, path):
        filename, basename, ext = self.get_filename(path)
        if os.path.isfile(filename):
            try:
                with open(filename, 'r') as content_file:
                    response_body = content_file.read()
                    return (response_body, self.supportedMIMEtypes[ext.strip(".")], "200 OK")
            except IOError, err:
                if err.errno is 13:
                    return ("<html><head><title>Error - Forbidden</title></head><body><h1>Error</h1><h3>%s Forbidden</h3></body></html>" % 
                        path, self.supportedMIMEtypes["html"], "403 Forbidden")
                else:
                    return ("<html><head><title>Error - Internal Server Error</title></head><body><h1>Error</h1><h3>%s Internal Server Error</h3></body></html>" % 
                        path, self.supportedMIMEtypes["html"], "500 Internal Server Error")
        else:
            logging.warn("file not found: %s" % filename)
            return ("<html><head><title>Error - File Not Found</title></head><body><h1>Error</h1><h3>%s Not Found</h3></body></html>" % path,
                self.supportedMIMEtypes["html"], "404 Not Found")

    def gen_response(self, url, method):
        path = urlparse(url).path
        if path == "" or method == "":
            body, mime_type, status = (
                "<html><head><title>Error - Bad Request</title></head><body><h1>Error</h1><h3>%s Bad Request</h3></body></html>" % path,
                    self.supportedMIMEtypes["html"], "400 Bad Request")
        elif method != "GET":
            body, mime_type, status = (
                "<html><head><title>Error - Not Implemented</title></head><body><h1>Error</h1><h3>%s Not Implemented</h3></body></html>" % path,
                    self.supportedMIMEtypes["html"], "501 Not Implemented")
        else:
            body, mime_type, status = self.get_file(path)
        date_header = "Date: %s\r\n" % self.rfc_1123_date()
        server_header = "Server: %s\r\n" % "python small server 1.0"
        type_header = "Content-Type: %s\r\n" % mime_type
        length_header = "Content-Length: %i\r\n" % len(body)
        headers = date_header + server_header + length_header + type_header
        if status.startswith("200"):
            last_modified_header = "Last-Modified: %s\r\n" % self.rfc_1123_date(os.path.getmtime(self.get_filename(path)[0]))
            headers += last_modified_header

        head = "HTTP/1.1 %s\r\n%s\r\n" % (status, headers)
        return head + body

    def handle_request(self, req, fd):
        parser = self.parse_request(req)
        req_headers = parser.get_headers()
        
        if not parser.is_headers_complete() or parser.is_partial_body() or not parser.is_message_complete():
            logging.error("Error parsing request")
            logging.info("Request: %s" % req)
            response = "<html><head><title>Error - Bad Request</title></head><body><h1>Error</h1><h3>Bad Request</h3></body></html>"
        response = self.gen_response(parser.get_url(), parser.get_method())
            
        logging.debug(response)
        self.clients[fd].send(response)

########### PARSING CONFIG FILE ################

    def parse_conf_file(self):
        configs = []
        try:
            with open('web.conf') as conf_file:
                for line in conf_file:
                    if line != "\n":
                        configs.append(line[0:-1] if line.endswith("\n") else line)
        except:
            logging.error("Could not find 'web.conf'. Exiting...")
            sys.exit(1)
        return configs

    def get_host(self, configs):
        # default is "localhost"
        return "localhost"

    def get_root(self, configs):
        # set host and root
        if configs[0].startswith("host"):
            try:
                return configs[0].split(' ')[2]    #return "web"...or whatever is in that position
            except:
                logging.error("Invalid HOST descriptor in web.conf.\n Usage: host [name] [path]\nExiting...")
                sys.exit(1)

    def get_supportedMIMEtypes(self, configs):
        # set supported MIME types
        types = {}
        for item in configs:
            if item.startswith("media"):
                vals = item.split(' ')
                # types.append(vals[1])
                types[vals[1]] = vals[2]
        return types

    def get_timeout(self, configs):
        for item in configs:
            if item.startswith("parameter"):
                return int(item.split(' ')[2])

