# Client-Server Protocol

This is how `client.py` and `server.py` talk to each other. Build against this document and reuse the helpers in `protocol.py` instead of calling `recv()` directly for text messages.

## Message format

TCP is a stream of bytes, so a single `recv()` can return half a message or two messages stuck together. To keep them apart, every text message is sent as:

```text
[4-byte big-endian length][UTF-8 text]
```

| Helper | What it does |
|---|---|
| `send_msg(sock, text)` | Sends one text message |
| `recv_msg(sock)` | Returns the next text message, or `None` if the other side closed the connection cleanly |
| `recv_exact(sock, size)` | Reads exactly `size` bytes. For small reads only |

Text messages are limited to 1 MB. If either side breaks the format, the helpers raise `ProtocolError`, and the server closes that connection.

**File contents are the exception.** They are sent as raw bytes with no length prefix, only at the points shown below, and only after both sides know the exact size. Read and write them in chunks of about 64 KB. Never load a whole file into memory, because files can be 2 GB.

## Replies

Every command ends with exactly one final reply, which starts with `OK` or `ERR`:

```text
OK notes.txt uploaded.
ERR notes.txt does not exist on the server.
```

Split it with `status, _, text = reply.partition(' ')`, then show `text` to the user. Replies can span several lines, as `dir` does.

`EXISTS`, `READY` and `SIZE <n>` are in-progress steps, not final replies. Always keep reading until you receive the final `OK` or `ERR`, or the next command's reply will be out of step.

## Conversations

`C:` is the client and `S:` is the server. Each line is one text message unless it says raw bytes.

### Upload

The client adds the file size as the last word of the command. The user only types `upload <filename>`.

```text
C: upload notes.txt 1048576
S: READY                      or ERR <reason>, which ends the command
C: <1048576 raw bytes>
S: OK notes.txt uploaded.     or ERR <reason>
```

If the file already exists, the server asks first:

```text
C: upload notes.txt 1048576
S: EXISTS
C: yes                        (ask the user; anything except yes or y cancels)
S: READY                      or ERR Upload cancelled.
C: <1048576 raw bytes>
S: OK notes.txt uploaded.     or ERR <reason>
```

### Download

```text
C: download notes.txt
S: SIZE 1048576               or ERR <reason>, which ends the command
C: GO                         (or CANCEL, and the server replies ERR Download cancelled.)
S: <1048576 raw bytes>
S: OK notes.txt downloaded.   or ERR <reason>
```

Read exactly the number of bytes given by `SIZE`, then read the final reply as a normal text message.

### Everything else

Each of these is one message from the client and one final reply from the server:

```text
C: delete notes.txt
C: dir
C: subfolder create photos
C: subfolder delete photos
C: help
```

`connect` and `quit` are handled by the client. To quit, close the socket.

File names can contain spaces. Paths inside the server storage use forward slashes, for example `photos/cat.png`. Subfolder names can't contain spaces.

## Login and encryption (coming next)

**This section isn't live yet.** Until it lands, the server accepts plain TCP connections with no login. It will be the only change to how a client connects.

- The connection will be wrapped in TLS right after it opens. `send_msg` and `recv_msg` work the same on a TLS socket, so keep the socket setup in one place in `client.py`. The exact code will be added here.
- Login will be the first message on the connection. No other command will work until it succeeds:

```text
C: login <username> <password>
S: OK <text>                  or ERR <reason>
```

After 3 failed attempts the server closes the connection. Read the password with `getpass`, and never print or log it.
