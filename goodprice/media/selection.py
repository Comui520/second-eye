from goodprice.media.types import MediaEvidence, MediaSelection


def select_media(images: list[MediaEvidence], frames: list[MediaEvidence],
                 image_quota: int, frame_quota: int, total_limit: int) -> MediaSelection:
    if not (0 <= image_quota <= 16 and 0 <= frame_quota <= 16 and 1 <= total_limit <= 16):
        raise ValueError("Invalid media input budget")
    seen = set()
    groups = []
    for group in (images, frames):
        unique = []
        for item in group:
            identity = (item.kind, item.content_hash, item.offer_key)
            if identity not in seen:
                seen.add(identity)
                unique.append(item)
        groups.append(unique)
    quotas = [image_quota, frame_quota]
    positions = [0, 0]
    selected = []
    # First distribute the requested shares fairly, then lend unused shares.
    for borrowing in (False, True):
        while len(selected) < total_limit:
            progressed = False
            for i in (0, 1):
                if (quotas[i] and positions[i] < len(groups[i])
                        and (borrowing or positions[i] < quotas[i])):
                    selected.append(groups[i][positions[i]])
                    positions[i] += 1
                    progressed = True
                    if len(selected) == total_limit:
                        break
            if not progressed:
                break
    ids = {x.id for x in selected}
    omitted = tuple(x.id for x in images + frames if x.id not in ids)
    limits = []
    if frames and not frame_quota:
        limits.append("frames_disabled")
    if images and not image_quota:
        limits.append("images_disabled")
    if omitted:
        limits.append("partial_media_coverage")
    return MediaSelection(tuple(selected), omitted, tuple(limits))
