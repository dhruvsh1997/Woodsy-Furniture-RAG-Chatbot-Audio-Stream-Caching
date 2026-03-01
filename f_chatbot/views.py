from django.shortcuts import render
import uuid


def index(request):
    """Landing page."""
    return render(request, "index.html")


def chat(request):
    """Chat page — passes a unique session ID to the template."""
    session_id = request.session.get("chat_session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        request.session["chat_session_id"] = session_id
    return render(request, "chat.html", {"session_id": session_id})