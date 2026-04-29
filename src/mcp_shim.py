
import asyncio
import os
import sys

async def pipe(reader, writer):
    try:
        while not reader.at_eof():
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except:
            pass

async def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <socket_path> <session_key>", file=sys.stderr)
        sys.exit(1)
        
    socket_path = sys.argv[1]
    session_key = sys.argv[2]
    
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
        
        # Send session key first as a single line
        writer.write((session_key + "\n").encode())
        await writer.drain()
        
        # We need to bridge stdio to this socket.
        # However, we must be careful with binary vs text and line buffering.
        # MCP is JSON-RPC over lines.
        
        loop = asyncio.get_event_loop()
        
        # Create a reader for stdin
        stdin_reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(stdin_reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        
        # Create a writer for stdout
        stdout_transport, stdout_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlDataProtocol, sys.stdout
        )
        stdout_writer = asyncio.StreamWriter(stdout_transport, stdout_protocol, stdin_reader, loop)
        
        # Pipe both ways
        await asyncio.gather(
            pipe(stdin_reader, writer),
            pipe(reader, stdout_writer)
        )
    except Exception as e:
        print(f"MCP Shim Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
