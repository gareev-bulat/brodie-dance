"""Plug-in point for the team's dance-detection model.

verity/agent.py calls DanceDetector().feed(frame) on the caller's camera at about 10 fps, in a worker thread, one frame at a time
(a slow model just sees fewer frames). Return None most of the time; when you detect a move, return a small dict, e.g.
{"move": "spin", "score": 7}. The agent then sends it to the web app (data topic "verity", type "dance") and asks the character to
react and rate it. Brodie's pose pipeline (facetime/perception.py, src/rtcoach/pose.py) is the natural base for landmarks.
"""


class DanceDetector:
    def feed(self, frame):
        """frame: livekit rtc.VideoFrame, RGBA (numpy: np.frombuffer(frame.data, np.uint8).reshape(frame.height, frame.width, 4))."""
        return None
