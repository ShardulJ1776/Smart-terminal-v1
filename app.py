import asyncio
import json
import logging
import os
import websockets

# Configure logging
logging.basicConfig(level=logging.INFO)

# In-memory storage for sessions and static links
# SESSIONS stores: { "session_id": { "sender": websocket, "viewers": {websockets} } }
SESSIONS = {}
# STATIC_LINKS stores: { "static_id": "session_id" }
STATIC_LINKS = {}


async def relay_server(websocket, path):
    """
    Handles incoming WebSocket connections, routing them as either a sender or a viewer.
    """
    logging.info(f"New connection from {websocket.remote_address}")

    try:
        # The first message from a client determines its role.
        initial_message = await websocket.recv()
        msg = json.loads(initial_message)
        
        role = msg.get("type")

        if role == "sender":
            await handle_sender(websocket, msg)
        elif role == "viewer":
            await handle_viewer(websocket, msg)
        else:
            logging.warning(f"Unknown role '{role}' from {websocket.remote_address}")
            await websocket.close(1003, "Unsupported role")

    except json.JSONDecodeError:
        logging.error("Failed to decode initial JSON message.")
        await websocket.close(1007, "Invalid JSON format")
    except websockets.exceptions.ConnectionClosed as e:
        logging.info(f"Connection closed before role assignment: {e.code}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during role assignment: {e}", exc_info=True)


async def handle_sender(websocket, msg):
    """
    Manages a connection from a local Python sender app.
    """
    session_id = msg.get("sessionId")
    static_id = msg.get("staticId")

    if not session_id:
        logging.warning("Sender connected without a session ID.")
        await websocket.close(1007, "Session ID is required")
        return

    # Create a new session
    SESSIONS[session_id] = {"sender": websocket, "viewers": set()}
    logging.info(f"Sender started session: {session_id}")

    # If the sender provides a static ID, link it to this session
    if static_id:
        STATIC_LINKS[static_id] = session_id
        logging.info(f"Static link '{static_id}' now points to session: {session_id}")

    await websocket.send(json.dumps({"status": "Session started successfully"}))

    try:
        # Main loop to relay data from sender to all viewers in the session
        async for message in websocket:
            viewers = SESSIONS.get(session_id, {}).get("viewers", set())
            if viewers:
                # Use asyncio.gather for concurrent sending
                await asyncio.gather(*(viewer.send(message) for viewer in viewers))

    except websockets.exceptions.ConnectionClosed as e:
        logging.info(f"Sender for session {session_id} disconnected: {e.code}")
    finally:
        # Cleanup: remove the session and any associated static link
        if session_id in SESSIONS:
            del SESSIONS[session_id]
            logging.info(f"Session closed: {session_id}")
        
        # Remove the static link if it points to the closed session
        if static_id and STATIC_LINKS.get(static_id) == session_id:
            del STATIC_LINKS[static_id]
            logging.info(f"Static link '{static_id}' has been cleared.")


async def handle_viewer(websocket, msg):
    """
    Manages a connection from a web browser viewer.
    """
    session_id = msg.get("sessionId")
    static_id = msg.get("staticId")

    # If a static ID is provided, resolve it to the current session ID
    if static_id:
        resolved_session_id = STATIC_LINKS.get(static_id)
        if not resolved_session_id:
            await websocket.send(json.dumps({"type": "error", "message": "Static link is not currently live."}))
            await websocket.close(1000, "Static link offline")
            return
        session_id = resolved_session_id

    if not session_id or session_id not in SESSIONS:
        await websocket.send(json.dumps({"type": "error", "message": "Session not found."}))
        await websocket.close(1000, "Session not found")
        return

    # Add the viewer to the correct session
    session = SESSIONS[session_id]
    session["viewers"].add(websocket)
    logging.info(f"Viewer joined session: {session_id}")

    try:
        await websocket.send(json.dumps({
            "type": "status", 
            "message": f"Connected to session: {session_id}"
        }))
        # Keep the connection alive to receive messages from the sender
        while True:
            await asyncio.sleep(60) # Keep connection open
    except websockets.exceptions.ConnectionClosed as e:
        logging.info(f"Viewer disconnected from session {session_id}: {e.code}")
    finally:
        # Remove the viewer upon disconnection
        session["viewers"].remove(websocket)
        logging.info(f"Viewer removed from session: {session_id}")


async def main():
    port = int(os.environ.get("PORT", 8080))
    # Render requires binding to 0.0.0.0
    async with websockets.serve(relay_server, "0.0.0.0", port):
        logging.info(f"Server started on port {port}")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server shutting down.")

