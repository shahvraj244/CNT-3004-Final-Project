# CNT3004 – Cloud Server Network Analysis Project

A multithreaded file-sharing system built with Python sockets (TCP). Clients connect to a server hosted on Google Cloud Platform (Compute Engine) to upload, download, delete, and list files. The project also collects network performance statistics.

**Group Members:** Vraj S, Nathan S, David P, Ines A

## Files

| File | Purpose |
|---|---|
| `server.py` | Multithreaded server: handles clients, authentication, file storage |
| `client.py` | Command-line client: connect, upload, download, delete, dir, subfolder |
| `analysis.py` | Collects transfer stats (data rate, transfer time, third metric) |

## Client Commands

- `connect <server IP> <port>`
- `upload <filename>`
- `download <filename>`
- `delete <filename>`
- `dir`
- `subfolder create <path>` / `subfolder delete <path>`

## How to Run

1. Install Python 3.
2. Start the server: `python server.py`
3. In another terminal, start the client: `python client.py`

## Notes

- Passwords and keys are never stored in this repository.
- Large test files (text, audio, video) are not committed to GitHub.
