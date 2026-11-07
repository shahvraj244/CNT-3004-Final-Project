#protocol.py
#Message format shared by the server and the client.
#
#TCP is a stream of bytes with no gaps between messages, so one recv() can
#return half a message or two messages stuck together. To keep them apart,
#every text message is sent as a 4-byte length followed by UTF-8 text.
#File contents are not wrapped this way: they travel as raw bytes after both
#sides have agreed on the size. PROTOCOL.md describes the full conversation.

import struct

HEADER = struct.Struct('!I')  # 4-byte unsigned length, in network byte order
MAX_MESSAGE_SIZE = 1024 * 1024  # 1 MB, far more than any command or reply needs


class ProtocolError(Exception):
    '''the other side sent something that breaks the message format'''


def ok(text):
    '''builds a success reply'''
    return f'OK {text}'


def err(text):
    '''builds a failure reply'''
    return f'ERR {text}'


def send_msg(sock, text):
    '''sends one text message with its length in front'''
    payload = text.encode('utf-8')
    if len(payload) > MAX_MESSAGE_SIZE:
        raise ProtocolError(f'message too long: {len(payload)} bytes')
    sock.sendall(HEADER.pack(len(payload)) + payload)


def recv_exact(sock, size):
    '''reads exactly size bytes, looping because recv() can return fewer.
    Only for small reads: file data should be streamed to disk in chunks.'''
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError('connection closed in the middle of a message')
        data += chunk
    return bytes(data)


def recv_msg(sock):
    '''returns the next text message, or None if the other side closed cleanly'''
    first = sock.recv(1)  # waits here; empty means a clean close between messages
    if not first:
        return None
    header = first + recv_exact(sock, HEADER.size - 1)
    (size,) = HEADER.unpack(header)
    # checked before reading, so a bogus length can't make us allocate gigabytes
    if size > MAX_MESSAGE_SIZE:
        raise ProtocolError(f'message too long: {size} bytes')
    try:
        return recv_exact(sock, size).decode('utf-8')
    except UnicodeDecodeError:
        raise ProtocolError('message is not valid UTF-8') from None
