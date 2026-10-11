"""Real 3D Verity models (fan-made, CC Attribution, Sketchfab) -> sprite frames the call avatar plays (verity/avatar.py).

  python -m verity.models fetch     # needs SKETCHFAB_API_TOKEN (sketchfab.com/settings/password -> API token) in the env
  python -m verity.models render    # GPU, headless (EGL): data/models3d/<name>/ -> web/assets/3d/<character>/{idle,talk}_NN.png

Rendering: the closed-mouth and open-mouth Verity models are turned slowly left to right and bobbed (IDLE_FRAMES poses), lit
from the top left, centered and scaled so the ball matches the 2D avatar's ball (avatar.CX, CY, R). The variants are the same
renders with Verity's yellow recolored to the character's color plus the character's gear (avatar.FACES static parts).
Credits (CC BY): every model is listed in MODELS and credited on the website.
"""
import glob, io, json, math, os, sys, urllib.request, zipfile
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, 'data', 'models3d')
OUT = os.path.join(ROOT, 'web', 'assets', '3d')
IDLE_FRAMES = 24

MODELS = {   # name: (sketchfab uid, title, author)
    'verity': ('aa5225880f5445e79a425eea391c42fb', 'Verity', 'c00lzer'),
    'verity_open': ('5ba54a0cbcb34b9fa2f85948bbbaa617', 'Verity Open Mouth [NOT RIGGED, MORE ACCURATE]', 'c00lzer'),
    'falsity': ('3661aadc8ff24c87af9a10c70839c2ec', 'Falsity', 'Astrix3D'),
    'monster': ('4a58bd4f61c14c089623a63c2e3138d7', 'Verity Monster', 'c00lzer'),
}


def fetch():
    token = os.environ.get('SKETCHFAB_API_TOKEN')
    if not token: raise SystemExit('set SKETCHFAB_API_TOKEN (sketchfab.com/settings/password -> API token) in .env.local')
    for name, (uid, title, author) in MODELS.items():
        dest = os.path.join(RAW, name)
        if glob.glob(os.path.join(dest, '**', '*.gltf'), recursive=True) + glob.glob(os.path.join(dest, '**', '*.glb'), recursive=True):
            print('have', name); continue
        req = urllib.request.Request(f'https://api.sketchfab.com/v3/models/{uid}/download', headers={'Authorization': f'Token {token}'})
        links = json.load(urllib.request.urlopen(req))
        fmt = links.get('glb') or links.get('gltf')
        if not fmt: print('no gltf/glb for', name, list(links)); continue
        data = urllib.request.urlopen(fmt['url']).read()
        os.makedirs(dest, exist_ok=True)
        if data[:4] == b'glTF': open(os.path.join(dest, 'model.glb'), 'wb').write(data)
        else: zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
        json.dump({'uid': uid, 'title': title, 'author': author, 'url': f'https://sketchfab.com/3d-models/{uid}', 'license': 'CC BY'},
                  open(os.path.join(dest, 'credit.json'), 'w'))
        print('fetched', name, f'({len(data) // 1024} KB) "{title}" by {author}')


def model_path(name):
    files = glob.glob(os.path.join(RAW, name, '**', '*.glb'), recursive=True) + glob.glob(os.path.join(RAW, name, '**', '*.gltf'), recursive=True)
    return files[0] if files else None


def render_frames(path, yaws, bobs, size=(360, 640)):
    """RGBA frames of one model: ball centered at (avatar.CX, avatar.CY) with radius avatar.R, turned by yaw and bobbed."""
    os.environ.setdefault('PYOPENGL_PLATFORM', 'egl')
    import trimesh, pyrender
    from verity.avatar import CX, CY, R
    tm = trimesh.load(path, force='scene')
    center, radius = tm.bounds.mean(axis=0), np.linalg.norm(tm.extents) / 2 / math.sqrt(3) * 1.08   # roughly the ball's radius
    w, h = size
    renderer = pyrender.OffscreenRenderer(w, h)
    yfov = math.radians(30); dist = radius / math.tan(yfov / 2) * (h / 2) / R     # the ball spans R pixels
    frames = []
    for yaw, bob in zip(yaws, bobs):
        scene = pyrender.Scene.from_trimesh_scene(tm, bg_color=[0, 0, 0, 0], ambient_light=[.45, .45, .45])
        rot = trimesh.transformations.rotation_matrix(math.radians(yaw), [0, 1, 0], center)
        for node in list(scene.mesh_nodes): node.matrix = rot @ scene.get_pose(node)
        cam = np.eye(4); cam[:3, 3] = center + [0, -(CY - h / 2) / R * radius + bob * radius, dist]   # ball lands on (CX, CY)
        scene.add(pyrender.PerspectiveCamera(yfov=yfov, aspectRatio=w / h), pose=cam)
        key = trimesh.transformations.rotation_matrix(math.radians(-35), [1, 0, 0]) @ trimesh.transformations.rotation_matrix(math.radians(-30), [0, 1, 0])
        scene.add(pyrender.DirectionalLight(intensity=4.0), pose=key)
        color, _ = renderer.render(scene, flags=pyrender.RenderFlags.RGBA)
        frames.append(color.copy())
    renderer.delete()
    return frames


def recolor(frame, rgb):
    """Verity's yellow -> rgb, keeping the shading (hue/saturation from rgb, brightness from the render)."""
    import cv2
    bgr = cv2.cvtColor(frame[..., :3], cv2.COLOR_RGB2BGR); hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    yellow = (hsv[..., 0] > 12) & (hsv[..., 0] < 40) & (hsv[..., 1] > 70) & (frame[..., 3] > 0)
    target = cv2.cvtColor(np.uint8([[rgb[::-1]]]), cv2.COLOR_BGR2HSV)[0, 0].astype(np.float32)
    hsv[yellow, 0] = target[0]; hsv[yellow, 1] = np.minimum(255, hsv[yellow, 1] * target[1] / 230)
    hsv[yellow, 2] = np.clip(hsv[yellow, 2] * (0.35 + 0.65 * target[2] / 255), 0, 255)
    out = frame.copy(); out[..., :3] = cv2.cvtColor(cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR), cv2.COLOR_BGR2RGB)
    return out


def render(closed=None, opened=None):
    import cv2
    from verity.avatar import FACES
    from verity.variants import VARIANTS
    closed, opened = closed or model_path('verity'), opened or model_path('verity_open')
    if not closed: raise SystemExit('no Verity model: run python -m verity.models fetch first')
    t = np.arange(IDLE_FRAMES) / IDLE_FRAMES
    yaws, bobs = 14 * np.sin(2 * math.pi * t), 0.04 * np.sin(4 * math.pi * t)
    idle = render_frames(closed, yaws, bobs)
    talk = render_frames(opened, yaws, bobs) if opened else idle
    for key, v in VARIANTS.items():
        d = os.path.join(OUT, key); os.makedirs(d, exist_ok=True)
        static, _ = FACES[v.get('face', 'plain')]
        for kind, frames in (('idle', idle), ('talk', talk)):
            for i, f in enumerate(frames):
                img = f if key == 'verity' else recolor(f, v['color'])
                if static: img = img.copy(); static(img, v['color'])
                cv2.imwrite(os.path.join(d, f'{kind}_{i:02d}.png'), cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA))
        print('rendered', key, 2 * IDLE_FRAMES, 'frames')


if __name__ == '__main__':
    {'fetch': fetch, 'render': render}[sys.argv[1] if len(sys.argv) > 1 else 'render']()
