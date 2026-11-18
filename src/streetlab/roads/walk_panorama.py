"""Choose a small set of centered panorama stations on a verified road graph."""

from collections import deque
from heapq import heappop, heappush
from math import atan2, degrees, dist, radians, tan

import numpy as np
from PIL import Image


def plan(data, count, spacing):
    step = data["step"]
    world = {(i, j): (i * step, j * step, z) for i, j, z in data["cells"]}
    reachable = {tuple(cell) for cell in data["reachable"]}
    edges = {cell: set() for cell in reachable}
    for a, b in data["links"]:
        a, b = tuple(a), tuple(b)
        if a in edges and b in edges:
            edges[a].add(b)
            edges[b].add(a)
    root = tuple(data["reachable"][0])

    clearance = {cell: 0 for cell in edges if len(edges[cell]) < 4}
    queue = deque(sorted(clearance))
    while queue:
        a = queue.popleft()
        for b in sorted(edges[a]):
            if b not in clearance:
                clearance[b] = clearance[a] + 1
                queue.append(b)

    # Penalize the edge of the recognized road while keeping every move on verified links.
    parent, cost, queue = {root: None}, {root: 0}, [(0, root)]
    while queue:
        total, a = heappop(queue)
        if total != cost[a]:
            continue
        for b in sorted(edges[a]):
            score = total + 1 + 4 / (1 + min(clearance[a], clearance[b])) ** 2
            if score < cost.get(b, float("inf")):
                parent[b], cost[b] = a, score
                heappush(queue, (score, b))

    routes = []
    for route in data["routes"]:
        target = tuple(round(value / step) for value in route[-1][:2])
        if target not in parent:
            continue
        path = []
        while target is not None:
            path.append(target)
            target = parent[target]
        if len(path) > 1:
            routes.append(path[::-1])
    if not routes:
        raise ValueError("No reachable route for panorama capture")
    routes.sort(key=len, reverse=True)
    network = {cell: set() for route in routes for cell in route}
    for route in routes:
        for a, b in zip(route, route[1:]):
            network[a].add(b)
            network[b].add(a)

    forks = sorted(cell for cell, neighbors in network.items() if len(neighbors) >= 3)
    selected = list(dict.fromkeys([root, *(route[-1] for route in routes), *forks]))[:count]
    # Add views shortly before and after the main fork, then cover the gaps.
    fork = min(forks, key=lambda cell: dist(cell, root)) if forks else root
    shoulders = []
    for a in sorted(network[fork]):
        before = fork
        for _ in range(max(0, round(spacing / step) - 1)):
            onward = network[a] - {before}
            if len(onward) != 1:
                break
            before, a = a, next(iter(onward))
        shoulders.append(a)
    for cell in shoulders:
        if cell not in selected and len(selected) < count:
            selected.append(cell)
    while len(selected) < min(count, len(network)):
        cell = max((c for c in network if c not in selected),
                   key=lambda c: (min(dist(c, other) for other in selected), clearance[c], c))
        selected.append(cell)

    captures = []
    review_cells = {fork, routes[0][min(len(routes[0]) - 1, max(1, len(routes[0]) // 3))]}
    # Pick a nearby selected station when the preferred point falls between stations.
    review_cells = {min(selected, key=lambda cell: dist(cell, target)) for target in review_cells}
    for cell in dict.fromkeys(cell for route in routes for cell in route if cell in selected):
        route = next(route for route in routes if cell in route)
        i = route.index(cell)
        a, b = route[max(0, i - 3)], route[min(len(route) - 1, i + 3)]
        heading = degrees(atan2(b[1] - a[1], b[0] - a[0]))
        name = f"pano_{len(captures):03d}"
        x, y, z = world[cell]
        captures.append({"name": name, "cell": list(cell), "yaw_degrees": heading,
                         "eye": [x, y, z + data["eye"]], "review": cell in review_cells})
    return captures, [[world[cell] for cell in route] for route in routes]


def perspective(panorama, yaw, width=512, height=384):
    """Extract a horizontal 85° perspective view, with yaw clockwise from panorama center."""
    source = np.asarray(panorama.convert("RGB"))
    pano_height, pano_width = source.shape[:2]
    xs = (2 * (np.arange(width) + 0.5) / width - 1) * tan(radians(85 / 2))
    ys = (1 - 2 * (np.arange(height) + 0.5) / height) * tan(radians(85 / 2)) * height / width
    x, y = np.meshgrid(xs, ys)
    lon = np.arctan(x) + radians(yaw)
    lat = np.arctan2(y, np.hypot(1, x))
    u = ((0.5 + lon / (2 * np.pi)) * pano_width - 0.5) % pano_width
    v = np.clip((0.5 - lat / np.pi) * pano_height - 0.5, 0, pano_height - 1)
    col, row = u.astype(int), v.astype(int)
    x_frac, y_frac = (u - col)[..., None], (v - row)[..., None]
    next_col, next_row = (col + 1) % pano_width, np.minimum(row + 1, pano_height - 1)
    top = (1 - x_frac) * source[row, col] + x_frac * source[row, next_col]
    bottom = (1 - x_frac) * source[next_row, col] + x_frac * source[next_row, next_col]
    return Image.fromarray(np.rint((1 - y_frac) * top + y_frac * bottom).astype(np.uint8))
