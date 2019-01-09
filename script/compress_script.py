#!/usr/bin/env python3

import sys
import re
import pathlib
import subprocess
import io


CHARSET_RANGE = re.compile(r"^\s+charset\s+'(.)'\s*,\s*'(.)'\s*,\s*((\$[A-Fa-f0-9][A-Fa-f0-9])|(\d+))\s*(;.*)?$")
CHARSET_SINGLE = re.compile(r"^\s+charset\s+'(.)'\s*,\s*((\$[A-Fa-f0-9][A-Fa-f0-9])|(\d+))\s*(;.*)?$")
CHARSET_SINGLE_NUM = re.compile(r"^\s+charset\s+(\$[A-Fa-f0-9][A-Fa-f0-9])\s*,\s*((\$[A-Za-z0-9][A-Za-z0-9])|(\d+))\s*(;.*)?$")

IF_DIRECTIVE = re.compile(r"^\s+if\s+([A-Za-z0-9_]+)\s+=\s+([0-9]+)\s*$")
ELSE_DIRECTIVE = re.compile(r"^\s+else\s*$")
ENDIF_DIRECTIVE = re.compile(r"^\s+endif\s*$")
DC_DIRECTIVE = re.compile(r"^\s+dc\.b\s+(.*)$")
DC_W_DIRECTIVE = re.compile(r"^\s+dc\.w\s+((\$[A-Fa-f0-9]{4})|(\d+))\s*(;.*)?$")

charset = {}
rcharset = {0xff: '\n-----\n', 0xfc: '\n', 0xfd: '\nVV\n'}

symbols = [a for a in sys.argv[1:] if '=' in a]
symbols = {arg: val for arg, val in map(lambda s: s.split('='), symbols)}
remainder = set([a for a in sys.argv[1:] if '=' not in a])
debug = False
if '--debug' in remainder:
	debug = True
	remainder.remove('--debug')
remainder = [pathlib.Path(f) for f in remainder]

print("Processing using symbols:", symbols)

basedir = pathlib.Path(sys.argv[0]).resolve().parent
print("Working directory:", basedir)

def decodenum(s):
	if s[0] == '$':
		return int(s[1:], 16)
	else:
		return int(s)

with open(basedir / "charset.asm", "rt", encoding="iso-8859-1") as f:
	for line in f:
		line = line.rstrip()
		r = CHARSET_RANGE.match(line)
		if r:
			base = decodenum(r.group(3))
			for count, ch in enumerate(range(ord(r.group(1)), ord(r.group(2)) + 1)):
				charset[chr(ch)] = base + count
				rcharset[base+count] = chr(ch)
			continue
			
		single = CHARSET_SINGLE.match(line)
		if single:
			charset[single.group(1)] = decodenum(single.group(2))
			rcharset[decodenum(single.group(2))] = single.group(1)
			continue
			
		single_num = CHARSET_SINGLE_NUM.match(line)
		if single_num:
			charset[chr(decodenum(single_num.group(1)))] = decodenum(single_num.group(2))
			rcharset[decodenum(single_num.group(2))] = chr(decodenum(single_num.group(1)))

print("Using charset table:")
for char, value in charset.items():
	if char.isprintable():
		print('\t"{0}": {1:02X}'.format(char, value))
	else:
		print('\t${0:02X}: {1:02X}'.format(ord(char[0]), value))

def normal(line, linenum, out):
	m = IF_DIRECTIVE.match(line)
	if m:
		var, val = m.group(1), m.group(2)
		if val == symbols.get(val, None):
			return look_for_else
		else:
			return look_for_else_skip
	process_raw_dc(line, linenum, out)
	return normal
	
def look_for_else_skip(line, linenum, out):
	m = ELSE_DIRECTIVE.match(line)
	if m:
		return look_for_endif
	m = ENDIF_DIRECTIVE.match(line)
	if m:
		return normal
	return look_for_else_skip
	
def look_for_else(line, linenum, out):
	m = ELSE_DIRECTIVE.match(line)
	if m:
		return look_for_endif_skip
	m = ENDIF_DIRECTIVE.match(line)
	if m:
		return normal
	process_raw_dc(line, linenum, out)
	return look_for_else
		
def look_for_endif_skip(line, linenum, out):
	m = ENDIF_DIRECTIVE.match(line)
	if m:
		return normal
	return look_for_endif_skip

def look_for_endif(line, linenum, out):
	m = ENDIF_DIRECTIVE.match(line)
	if m:
		return normal
	process_raw_dc(line, linenum, out)
	return look_for_endif
		
def process_raw_dc(line, linenum, out):
	m = DC_DIRECTIVE.match(line)
	if m:
		process_dc(m.group(1), linenum, out)
		return
	m = DC_W_DIRECTIVE.match(line)
	if m:
		s = m.group(1)
		decoded = decodenum(s)
		out.write(bytes([(decoded&0xFF00)>>8,decoded&0xFF]))
	else:
		#print(line)
		pass

def process_dc(dc, linenum, out):
	state = scanning
	buf = []
	for ch in dc:
		state = state(ch, buf, linenum, out)
	state(None, buf, linenum, out)

def syntax_error(ch, linenum):
	print("Syntax error: illegal character", ch, "@", linenum)
	sys.exit(-1)
	
def comment(ch, buf, linenum, out):
	return comment

hexdigits = set("ABCDEFabcdef0123456789")
def hex_digit(ch, buf, linenum, out):
	if ch is not None and ch in hexdigits:
		buf.append(ch)
		return hex_digit
	else:
		translated = decodenum(''.join(buf))
		out.write(bytes([translated]))
		buf.clear()
		return scanning(ch, buf, linenum, out)
	
def in_quote(ch, buf, linenum, out):
	if ch is None:
		syntax_error("newline", linenum)
	if ch == '"':
		return maybe_quote_char
	buf.append(ch)
	return in_quote

def maybe_quote_char(ch, buf, linenum, out):
	if ch is not None and ch == '"':
		buf.append('"')
		return in_quote
	else:
		# translate unknown chars to space
		out.write(bytes([charset.get(ch, charset.get(' ', 0)) for ch in buf]))
		buf.clear()
		return scanning(ch, buf, linenum, out)

def dec_digit(ch, buf, linenum, out):
	if ch is not None and ch.isdigit():
		buf.append(ch)
		return dec_digit
	else:
		translated = decodenum(''.join(buf))
		out.write(bytes([translated]))
		buf.clear()
		return scanning(ch, buf, linenum, out)

def scanning(ch, buf, linenum, out):
	if ch is None:
		return
	if ch == '$':
		buf.append('$')
		return hex_digit
	if ch == ',':
		return scanning
	if ch == '"':
		return in_quote
	if ch == ';':
		return comment
	if ch.isdigit():
		buf.append(ch)
		return dec_digit
	if ch.isspace():
		return scanning
	syntax_error(ch, linenum)
	
def dump(f):
	print(f)
	print('==================')
	with open(f, "rb") as debugf:
		while True:
			chunk = debugf.read(8192)
			if not chunk:
				break
			changed = [rcharset.get(b, '${0:02X}\n'.format(b)) for b in chunk]
			print(''.join(changed), sep='', end='')

if len(remainder) == 0:
	remainder = basedir.glob('dialogue *.asm')
for fname in remainder:
	print("processing", fname)
	dest = fname.with_suffix('.bin.unc')
	dest2 = dest
	idx = 0
	while dest2.exists():
		dest2 = dest.with_suffix('.unc.{}'.format(idx))
		idx += 1
	if idx != 0:
		print("backing up", dest, "to", dest2)
		dest.rename(dest2)
	state = normal
	with open(fname, "rt", encoding="iso-8859-1") as f, open(dest, "wb") as out:
		for linenum, line in enumerate(f):
			line = line.rstrip()
			state = state(line, linenum, out)
			if debug:
				print(out.tell(), ':', line)
		if state != normal:
			print("Unterminated if in ", fname, ", aborting", sep='')
			sys.exit(-1)
		if out.tell() % 2 != 0:
			out.write(bytes([0]))
		print("done processing", fname, "to", dest)
	if debug:
		dump(dest)
		if dest2.exists():
			dump(dest2)
	# compress
	bindest = fname.with_suffix('.bin')
	bindest2 = bindest
	idx = 0
	while bindest2.exists():
		bindest2 = bindest2.with_suffix('.bin.{}'.format(idx))
		idx += 1
	if idx != 0:
		print("backing up", bindest, "to", bindest2)
		bindest.rename(bindest2)
	subprocess.run(['koscmp', str(dest.resolve(strict=True)), str(bindest.resolve())])
	# pad to 16 bytes
	with open(bindest, "r+b") as f:
		f.seek(0, io.SEEK_END)
		sz = f.tell()
		if sz % 16 != 0:
			f.write(bytes(sz%16))
