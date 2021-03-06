#!/usr/bin/env python2

import alsaaudio
import numpy
import random
import time
import sys

from twinklclient import TwinklSocket, TwinklMessage


WIDTH = 6
HEIGHT = 8
AUDIO_RATE = 16000 # sampling rate in Hz
WINDOW_SIZE = 14 # average fft over WINDOW_SIZE audio frames

BOX_MAP = [
			[357,  18, 369, 186, 249, 228,  51],
			[279,   9,  57, 159, 300, 108, 204],
			[261,  42, 183, 201, 273, 246,  15],
			[306, 168,  24, 138, 309, 165,  39],
			[258, 222,  87, 363, 291, 231, 243],
			[252, 114, 180,  75, 282, 141,  33],
			[264, 288, 120, 135, 255,  99, 105],
			[285, 207, 102,  45, 297, 216,  63],
		]

COLORS = [
			[50, 255, 50], [50, 255, 120], [50, 251, 255], [50, 120, 255],
			[50, 50, 255], [180, 50, 255], [255, 50, 198], [255, 50, 50]
		]


class Background:
	"""clear the light wall to a pseudorandomly changing/fading solid color"""

	def __init__(self, twinkl_output):
		self._current_bg_color = [ 0, 0, 0 ]
		self._target_bg_color = [ 128, 128, 128 ]
		self._bg_time = 0
		self._out = twinkl_output


	def clear(self):
		for i in range(3):
			if self._current_bg_color[i] < self._target_bg_color[i]:
				self._current_bg_color[i] = self._current_bg_color[i] + 1
			elif self._current_bg_color[i] > self._target_bg_color[i]:
				self._current_bg_color[i] = self._current_bg_color[i] - 1

			if self._current_bg_color[i] > 128:
				self._current_bg_color[i] = 128
			elif self._current_bg_color[i] < 0:
				self._current_bg_color[i] = 0

		self._bg_time = self._bg_time + 1
		if self._bg_time == 64:
			for i in range(3):
				self._target_bg_color[i] = random.randint(0, 128)
			self._bg_time = 0

		for x in range(WIDTH):
			for y in range(HEIGHT):
				color = self._out.get_box(x, y)

				if y != HEIGHT - 1:
					color_below = self._out.get_box(x, y + 1)
				else:
					color_below = self._current_bg_color

				for i in range(3):
					# fade into BG by adding just a little of the current BG color,
					# add color from pixel below for a "upward flowing" effect
					color[i] = int(0.744 * color[i] + 0.056 * self._current_bg_color[i] + 0.2 * color_below[i])

				self._out.set_box(x, y, color[0], color[1], color[2])


def audio_from_raw(raw):
	"""convert bytewise signed 16bit little endian to int list, only take one stereo channel"""
	out = []
	sample_byte = 0
	current = 0

	for value in raw:
		value = ord(value[0])
		if sample_byte == 0:
			current = value
			sample_byte = 1
		elif sample_byte == 1:
			sign = value & 0x80
			current = current + 256 * (value & 0x7F)
			if sign:
				current =  -((~current & 0x7FFF) + 1)
			out.append(current)
			sample_byte = 2
		elif sample_byte == 2: # skip other stereo channel
			sample_byte = 3
		else:
			sample_byte = 0
	return out


class Twinkl_output:
	"""Wrapper class for TwinklSocket/TwinklMessage that keeps a frame buffer which allows write and
	read access and takes care of mapping x/y coordinates to boxes"""


	def __init__(self, hostname, port, priority):
		self._socket = TwinklSocket(hostname, port)
		self._msg = TwinklMessage()

		self._msg.set_priority(int(priority))

		self._channels = {}


	def set_box(self, x,y,r,g,b):
		if x >= 0 and y >= 0 and x < WIDTH and y < HEIGHT:
			base_address = BOX_MAP[y][x]
			self._channels[base_address] = r
			self._channels[base_address + 1] = g
			self._channels[base_address + 2] = b


	def get_box(self, x, y):
		base_address = BOX_MAP[y][x]
		try:
			res = [
					self._channels[base_address],
					self._channels[base_address + 1],
					self._channels[base_address + 2]
			]
		except KeyError, e:
			# If the array is still uninitialized, just return [0,0,0]
			res = [ 0, 0, 0 ]
		return res


	def send(self):
		for _, i in enumerate(self._channels):
			self._msg[i] = self._channels[i]
		self._socket.send(self._msg)


class Fft_output:
	"""aggregate several fft'ed samples, does postprocessing to make it look nice and outputs
	the aggregated result"""


	def __init__(self, width, height, windowsize, twinkl_output):
		self._background = Background(twinkl_output)
		self._width = width
		self._height = height
		self._windowsize = windowsize
		self._out = twinkl_output

		self._count = 0
		self._data = [0] * width


	def add(self, data):
		"""add a set of fft data to the internal store, output the result if enough data is
			available"""

		binsize = 2

		abss = numpy.absolute(data[0:binsize * self._width])

		# sum fft values into bins
		absum = [0] * self._width
		for i in range(1, self._width * binsize):
			absum[i / binsize] = absum[i / binsize] + abss[i]

		for i in range(self._width):
			self._data[i] = self._data[i] + absum[i]

		self._count = self._count + 1
		if (self._count == self._windowsize):
			self.output_twinkl()
			self._count = 0
			for i in range(self._width):
				self._data[i] = 0


	def output_twinkl(self):
		"""output graph to twinkl client"""
		# correct for disproportionately large first and second column
		self._data[0] = self._data[0] / 2
		self._data[1] = self._data[1] / 2.5
		self._data[2] = self._data[2] / 1.5

		self._background.clear()
		for col in range(self._width):
			normalized = min(int(self._data[col] / (self._height * self._windowsize * 700)), self._height)
			color = COLORS[normalized-1]
			for row in range(self._height - normalized, self._height):
				self._out.set_box(col, row, color[0], color[1], color[2])

		self._out.send()


def init_audio(rate):
	"""init sound input to 16bit little endian and user defined sampling rate"""
	ain = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NORMAL)
	ain.setformat(alsaaudio.PCM_FORMAT_S16_LE)
	ain.setrate(rate)
	ain.setperiodsize(64)

	return ain


def main():
	if len(sys.argv) != 3:
		print "Usage: %s host priority" % sys.argv[0]
		sys.exit(1)

	output = Twinkl_output(sys.argv[1], "1337", sys.argv[2])

	ain = init_audio(AUDIO_RATE)
	fft_out = Fft_output(WIDTH, HEIGHT, WINDOW_SIZE, output)

	while True:
		data = ain.read();
		audio = audio_from_raw(data[1])
		fft = numpy.fft.fft(audio)
		fft_out.add(fft)

main()
