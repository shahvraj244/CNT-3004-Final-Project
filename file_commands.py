#file_commands.py
#Cheks if the command entered by the users are valid and returns the reply text to the server.py file 

import json
import os
import shutil
import threading

from protocol import err, ok, recv_msg, send_msg

#everything the server stores lives next to this file, not in whatever folder the server
#was started from. CNT_DATA_DIR can point it somewhere else (the tests use a temp folder)
DATA_DIR = os.path.abspath(os.environ.get('CNT_DATA_DIR', os.path.dirname(os.path.abspath(__file__))))
STORAGE = os.path.join(DATA_DIR, 'server_storage')
REGISTRY_FILE = os.path.join(DATA_DIR, 'server_registry.json')  # remembers original name -> stored ID, survives restarts
CHUNK_SIZE = 65536  #size for a file for upload and download
PROMPT_TIMEOUT = 120  #seconds the client has to answer EXISTS (yes/no) or SIZE (GO/CANCEL)
TRANSFER_TIMEOUT = 60  #seconds a transfer can go without moving any data before it is dropped
MIN_FREE_SPACE = 100 * 1024 * 1024  #an upload must leave at least 100 MB free on the server's disk
os.makedirs(STORAGE, exist_ok=True) #creates the storage directory if it doesn't exist


class TransferAborted(Exception):
    '''a transfer broke after raw file bytes started moving. The connection can't be used
    anymore, because there is no way to tell where the file ends and the next message starts'''


def remove_partial_uploads():
    '''deletes .part files left behind if the server stopped in the middle of an upload'''
    for root, _, files in os.walk(STORAGE):
        for f in files:
            if f.endswith('.part'):
                os.remove(os.path.join(root, f))

remove_partial_uploads()  #safe at startup: no upload can be running yet

#types of file extensions that are supported by the server, any other file type will be rejected
TYPES = {
    '.txt':  'txt',
    '.pdf':  'txt',
    '.docx': 'txt',
    '.mp3':  'aud',
    '.wav':  'aud',
    '.mp4':  'vid',
    '.mov':  'vid',
}
'''Naming convention to store files on the server: each type has a prefix and a counter, e.g. txt001, aud002, vid003.'''
# counters only go up, so a number is never used twice, even after a delete
registry = {'counters': {'txt': 0, 'aud': 0, 'vid': 0}, 'files': {}}
if os.path.exists(REGISTRY_FILE):
    with open(REGISTRY_FILE) as f:
        registry = json.load(f)
registry_lock = threading.Lock()  # two uploads at once must not get the same number

#files being uploaded/downloaded right now. Several downloads can share a file,
#but an upload or a delete needs the file to itself.
#in_use maps a path to how many downloads have it, or to WRITING
WRITING = -1
in_use = {}
in_use_lock = threading.Lock()  #threads share in_use, so a lock protects it

def save_registry():
    '''writes the registry to disk, caller must hold registry_lock'''
    with open(REGISTRY_FILE, 'w') as f:
        json.dump(registry, f)

def assign_id(name, prefix):
    '''new name gets the next number for its type (txt001, txt002...), an existing name keeps its ID'''
    with registry_lock:
        if name not in registry['files']:
            registry['counters'][prefix] += 1
            registry['files'][name] = f"{prefix}{registry['counters'][prefix]:03d}"
            save_registry()
        return registry['files'][name]

def real_path(path, name):
    '''where the file really is on disk (original name -> stored ID), None if not stored'''
    stored_id = registry['files'].get(name)
    return os.path.join(os.path.dirname(path), stored_id) if stored_id else None

def claim_read(path):
    '''lets a download share the file with other downloads, False if an upload or delete has it'''
    with in_use_lock:
        holders = in_use.get(path, 0)
        if holders == WRITING:
            return False
        in_use[path] = holders + 1
        return True

def claim_write(path):
    '''gives an upload or delete the file to itself, False if anyone else is using it'''
    with in_use_lock:
        if path in in_use:
            return False
        in_use[path] = WRITING
        return True

def release(path):
    '''gives back one claim on the file'''
    with in_use_lock:
        holders = in_use.get(path, 0)
        if holders > 1:
            in_use[path] = holders - 1  #other downloads are still reading it
        else:
            in_use.pop(path, None)  #last download finished, or the upload/delete is done

def safe_path(name):
    '''converts a filename to a safe path within the STORAGE directory'''
    full = os.path.abspath(os.path.join(STORAGE, name)) #path to file/folder
    root = os.path.abspath(STORAGE)
    # root + os.sep stops sibling folders like server_storage_evil from slipping through
    if full == root or not full.startswith(root + os.sep):
        raise ValueError(f"Unsafe path: {name}")
    return full

def receive_file(connection, file_path, size):
    '''receives exactly size bytes from the client and writes them to file_path'''
    remaining = size
    with open(file_path, 'wb') as f:
        while remaining > 0:
            # never ask for more than what is left, so we don't read into the next message
            chunk = connection.recv(min(CHUNK_SIZE, remaining))
            if not chunk:
                return False  # client disconnected mid-upload
            f.write(chunk)
            remaining -= len(chunk)
    return True

def do_upload(message, connection):
    '''upload <name> <size>: asks before overwriting, then receives the file'''
    parts = message.split()
    if len(parts) < 3 or not parts[-1].isdigit():  #user adds the size as the last word
        return err('Usage: upload <filename>')
    name = ' '.join(parts[1:-1])
    size = int(parts[-1])
    try:
        path = safe_path(name)
    except ValueError:
        return err('That file name is not allowed.')
    if not os.path.isdir(os.path.dirname(path)):
        return err('That folder does not exist on the server.')
    if os.path.isdir(path):
        return err(f'{name} is a folder, not a file.')
    prefix = TYPES.get(os.path.splitext(name)[1].lower())  #type of files audio, video, or text
    if prefix is None:
        return err('Only text, audio and video files are supported.')
    #checked before any bytes move, so a full disk gives a clean error instead of a broken upload
    if shutil.disk_usage(STORAGE).free < size + MIN_FREE_SPACE:
        return err(f'Not enough space on the server for {name}.')
    if not claim_write(path):
        return err(f'{name} is being used right now. Try again in a moment.')
    temp = path + '.part'
    try:
        if name in registry['files']:  #a file with this name is already stored
            send_msg(connection, 'EXISTS')  #client asks the user yes/no and sends the answer back
            connection.settimeout(PROMPT_TIMEOUT)  #so a user who walks away doesn't lock the file forever
            answer = (recv_msg(connection) or '').strip().lower()
            if answer not in ('yes', 'y'):
                return err('Upload cancelled.')
        send_msg(connection, 'READY')  #tells the client to start sending the file bytes
        connection.settimeout(TRANSFER_TIMEOUT)  #a client that stops sending is dropped
        #save to a temp file first so a failed upload never destroys the old file
        try:
            received = receive_file(connection, temp, size)
        except OSError as e:  #disk error, timeout or reset while the file bytes were arriving
            raise TransferAborted(f'upload of {name} stopped halfway ({e})') from e
        if not received:
            return err('Upload failed: connection lost.')
        stored_id = assign_id(name, prefix)
        os.replace(temp, os.path.join(os.path.dirname(path), stored_id))  # saved under its ID, e.g. txt001
        return ok(f'{name} uploaded as {stored_id}.')
    finally:
        if os.path.exists(temp):
            os.remove(temp)  # cleans up half-finished uploads
        release(path)
        connection.settimeout(None)  #no time limit while waiting for the next command

def do_download(message, connection):
    '''download <name>: sends the size, waits for GO, then sends the file'''
    parts = message.split()
    if len(parts) < 2:
        return err('Usage: download <filename>')
    name = ' '.join(parts[1:])
    try:
        path = safe_path(name)
    except ValueError:
        return err('That file name is not allowed.')
    real = real_path(path, name)
    if real is None or not os.path.isfile(real):
        return err(f'{name} does not exist on the server.')
    if not claim_read(path):  #other downloads are fine, an upload or delete in progress is not
        return err(f'{name} is being used right now. Try again in a moment.')
    try:
        send_msg(connection, f'SIZE {os.path.getsize(real)}')  # client needs the size
        connection.settimeout(PROMPT_TIMEOUT)  #so a client that never answers doesn't hold the file
        answer = (recv_msg(connection) or '').strip()
        if answer != 'GO':
            return err('Download cancelled.')
        connection.settimeout(TRANSFER_TIMEOUT)  #a client that stops reading is dropped
        try:
            with open(real, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    connection.sendall(chunk)
        except OSError as e:  #disk error, timeout or reset while the file bytes were leaving
            raise TransferAborted(f'download of {name} stopped halfway ({e})') from e
        return ok(f'{name} downloaded.')
    finally:
        release(path)
        connection.settimeout(None)  #no time limit while waiting for the next command

def do_delete(message):
    '''delete <name>: removes a file unless it is missing or in use'''
    parts = message.split()
    if len(parts) < 2:
        return err('Usage: delete <filename>')
    name = ' '.join(parts[1:])
    try:
        path = safe_path(name)
    except ValueError:
        return err('That file name is not allowed.')
    real = real_path(path, name)
    if real is None or not os.path.isfile(real):
        return err(f'{name} does not exist.')
    if not claim_write(path):
        return err(f'{name} is being used right now, so it was not deleted.')
    try:
        os.remove(real)
        with registry_lock:
            del registry['files'][name]  # the counter stays, so this number is never reused
            save_registry()
        return ok(f'{name} deleted.')
    finally:
        release(path)

def handle_command(message, connection): #function to check what client's input and return the reply text
    '''checks what client's input and returns the reply text'''
    parts = message.strip().split()
    if not parts:
        return err('No command received')
    command = parts[0].lower() #first word set to command, rest are args

    if command == 'help':
        return ok(
        'Server commands:\n'
        '  upload <file> <size>       send a file to the server\n'
        '  download <file>            fetch a file from the server\n'
        '  delete <file>              remove a stored file\n'
        '  dir                        list files and folders\n'
        '  subfolder create|delete <name>   manage folders\n'
        '  help                       show this message\n'
        'Client commands (type these at your own prompt, not here):\n'
        '  connect                    open the connection\n'
        '  quit                       close the connection and exit'
    )

    if command == 'upload':
        return do_upload(message, connection)

    if command == 'download':
        return do_download(message, connection)

    if command == 'delete':
        return do_delete(message)
    
    if command == 'dir':
        items = []
        for root, dirs, _ in os.walk(STORAGE):
            for d in dirs:
                items.append(os.path.relpath(os.path.join(root, d), STORAGE) + '/')
        with registry_lock:  #files are listed from the registry, so users see their own names
            items += [f'{name} ({stored_id})' for name, stored_id in registry['files'].items()]
        return ok('\n'.join(sorted(items)) if items else 'The server has no files yet.')

    if command == 'subfolder': #function to create or delete subfolders in the server storage directory
        if len(parts) != 3 or parts[1].lower() not in ('create', 'delete'):
            return err('Usage: subfolder create <name>  or  subfolder delete <name>')
        action, name = parts[1].lower(), parts[2]
        try:
            path = safe_path(name)
        except ValueError:
            return err('That folder name is not allowed.')
        try:
            if action == 'create':
                os.makedirs(path)
                return ok(f'Folder {name} created.')
            os.rmdir(path)
            return ok(f'Folder {name} deleted.')
        except FileExistsError:
            return err(f'Folder {name} already exists.')
        except FileNotFoundError:
            return err(f'Folder {name} does not exist.')
        except NotADirectoryError:
            return err(f'{name} is not a folder.')
        except OSError:
            return err(f'Folder {name} is not empty, so it was not deleted.')

    return err(f'Unknown command "{parts[0]}". Type help to see the commands.')