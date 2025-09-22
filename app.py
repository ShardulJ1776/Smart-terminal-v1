import asyncio
import json
import websockets
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# This dictionary will store our "live sessions".
# The key will be a session_id (e.g., "abc-123").
# The value will be a set of all connected viewers (websocket connections).
SESSIONS = {}

async def relay_server(websocket, path):
    """
    This function handles each incoming WebSocket connection.
    """
    logging.info(f"New connection from {websocket.remote_address}")
    session_id = None
    is_sender = False

    try:
        # The first message from any client tells us what session it's interested in.
        initial_message = await websocket.recv()
        data = json.loads(initial_message)
        session_id = data.get("sessionId")

        if not session_id:
            logging.warning("Connection closed: No session ID provided.")
            await websocket.close(1008, "Session ID is required")
            return

        # The 'type' tells us if this is the Python App sending data ("sender")
        # or a browser viewing data ("viewer").
        client_type = data.get("type", "viewer")

        if client_type == "sender":
            is_sender = True
            logging.info(f"Sender connected for session: {session_id}")
            # A sender doesn't need to be in the session's viewer list.
            # It just sends data.
            await websocket.send(json.dumps({"status": "Sender connected successfully"}))

            # Listen for subsequent messages from the sender to relay
            async for message in websocket:
                if session_id in SESSIONS:
                    # This is the serial data from the sender's local app
                    log_data = json.loads(message)
                    logging.info(f"Relaying data for session {session_id}: {log_data.get('data')[:30]}...")
                    
                    # Create a list of tasks to send the message to all viewers
                    forward_tasks = [viewer.send(json.dumps(log_data)) for viewer in SESSIONS[session_id]]
                    if forward_tasks:
                        await asyncio.gather(*forward_tasks)

        else: # This is a viewer (a web browser)
            logging.info(f"Viewer connected for session: {session_id}")
            
            # If this is the first viewer for this session, create a new set for it.
            if session_id not in SESSIONS:
                SESSIONS[session_id] = set()
            
            # Add this viewer to the set of viewers for this session.
            SESSIONS[session_id].add(websocket)
            await websocket.send(json.dumps({"status": "Connected to session. Waiting for data..."}))

            # Keep the connection open, but we don't expect more messages from the viewer.
            await websocket.wait_closed()

    except websockets.exceptions.ConnectionClosed as e:
        logging.info(f"Connection closed for {websocket.remote_address} - Reason: {e.reason} ({e.code})")
    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)
    finally:
        # When the connection is closed, remove the viewer from the session.
        if session_id and not is_sender and session_id in SESSIONS:
            SESSIONS[session_id].remove(websocket)
            logging.info(f"Viewer disconnected from session: {session_id}")
            # If that was the last viewer, we can clean up the session.
            if not SESSIONS[session_id]:
                logging.info(f"Session closed: {session_id} (no more viewers)")
                del SESSIONS[session_id]
        elif is_sender:
            logging.info(f"Sender for session {session_id} disconnected.")


async def main():
    # Get the port from environment variables, with a default for local testing
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Starting WebSocket server on port {port}")
    async with websockets.serve(relay_server, "0.0.0.0", port):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
