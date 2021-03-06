#!/usr/bin/env python
#-*- coding: utf-8 -*-

import os
import sys
import string
import random
import socket
import ConfigParser
import msgpackrpc
import traceback
import consistent_hash
import importlib

class Client(object):
	#@param conffile : str
	#@param caller : str
	def __init__(self, conffile, caller=''):
		self.addrs = {}
		self.weight = {}
		self.bakaddrs = {}
		self.shardsec = {}
		self.downs = []
		self.clientkey = 0
		self.caller = caller
		self.rpc_proto = None

		try:
			self.conf = ConfigParser.ConfigParser()
			self.conf.read(conffile)
			self.mode = self.conf.get('server', 'mode')
			if self.mode == 'sharding':
				self.shardsum = self.conf.getint('server', 'shardsum')

			for i in xrange(self.conf.getint('server', 'sum')):
				ip = self.conf.get('server%d' % (i), 'ip')
				port = self.conf.getint('server%d' % (i), 'port')
				self.addrs['%s:%d' % (ip, port)] = msgpackrpc.Address(ip, port)
				if self.mode == 'hashring':
					self.weight['%s:%d' % (ip, port)] = self.conf.getint('server%d' % (i), 'weight')
				elif self.mode == 'sharding':
					self.bakaddrs['%s:%d' % (ip, port)] = msgpackrpc.Address(
							self.conf.get('server%d' % (i), 'ip_bak'),
							self.conf.getint('server%d' % (i), 'port_bak'))
					self.shardsec['%s:%d' % (ip, port)] = (
							self.conf.getint('server%d' % (i), 'shardbegin'),
							self.conf.getint('server%d' % (i), 'shardend'))
				else:
					raise TypeError('type of server mode isnot correct!')
			self._build_client()
			self._get_rpc_proto()
		except Exception, e:
			print traceback.format_exc()
			sys.exit()

	def _get_rpc_proto(self):
		absolute_path = os.path.dirname(os.path.abspath(os.path.join(__file__, '..')))
		for python_path in os.getenv('PYTHONPATH').split(':'):
			if absolute_path.startswith(python_path):
				rootpath = os.path.relpath(absolute_path, python_path).replace(os.path.sep, '.')
				self.rpc_proto = importlib.import_module('.rpc_proto.${app}_rpc_proto', rootpath)
				break
		else:
			raise IOError('%s is not in PYTHONPATH' % (__file__))

	def setShardingID(self, shardingID):
		if type(shardingID) != type(1):
			raise TypeError('shardingID isnot int type!')
		self.clientkey = shardingID

	def _build_client(self):
		if self.mode == 'hashring':
			self.con_hash = consistent_hash.ConsistentHash(self.weight)
			self._set_hashring_client()
		elif self.mode == 'sharding':
			self._set_sharding_client()

	def _rebuild_hashring_client(self):
		recovers = []
		for down_server in self.downs:
			arr = down_server.split(':')
			if self._isopen(arr[0], int(arr[1])):
				recovers.append(down_server)
				self.con_hash.add_nodes({down_server : self.weight[down_server]})
		for s in recovers:
			if s in self.downs:
				self.downs.remove(s)
		self._set_hashring_client()

	def _set_hashring_client(self):
		self.clientkey = self._randomstr()
		self.con_server = self.con_hash.get_node(self.clientkey)
		self.client = msgpackrpc.Client(
				address = self.addrs[self.con_server],
				timeout = 3)

	def _set_sharding_client(self):
		segnum = self.clientkey % self.shardsum
		for server in self.shardsec:
			if segnum >= self.shardsec[server][0] and \
					segnum <= self.shardsec[server][1]:
				self.con_server = server
				break
		self.client = msgpackrpc.Client(
				address = self.addrs[self.con_server],
				timeout = 3)
		self.isMasterLive = True

	def _randomstr(self, randomlength=8):
		a = list(string.ascii_letters)
		random.shuffle(a)
		return ''.join(a[:randomlength])

	def _isopen(self, ip, port):
		sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		sk.settimeout(1)
		isopen = False
		try:
			sk.connect((ip, port))
			isopen = True
		except Exception:
			isopen = False
		sk.close() 
		return isopen

	def _hashring_failover(self):
		self.downs.append(self.con_server)
		try:
			self.con_hash.del_nodes([self.con_server])
		except Exception as e:
			pass
		if len(self.downs) > len(self.addrs)/2:
			self._rebuild_hashring_client()
		else:
			self._set_hashring_client()
		if len(self.downs) == len(self.addrs):
			return False
		return True

	def _sharding_failover(self):
		if self.isMasterLive:
			self.client = msgpackrpc.Client(
					address = self.bakaddrs[self.con_server],
					timeout = 3)
			self.isMasterLive = False
		else:
			arr = self.con_server.split(':')
			if self._isopen(arr[0], int(arr[1])):
				self.client = msgpackrpc.Client(
						address = self.addrs[self.con_server],
						timeout = 3)
				self.isMasterLive = True
			else:
				return False
		return True

	def _failover(self):
		if self.mode == 'hashring':
			return self._hashring_failover()
		elif self.mode == 'sharding':
			return self._sharding_failover()

