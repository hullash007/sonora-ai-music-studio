"""Shared duration policy. Candidate lengths are not a claim of live validation."""
CANDIDATE_DURATIONS = (30, 60, 90, 120)
DEFAULT_DURATIONS = (30,)

def configured_durations(value="30"):
    try:
        result = tuple(int(x.strip()) for x in value.split(","))
    except (ValueError, AttributeError):
        raise ValueError("Invalid studio duration configuration") from None
    if not result or len(set(result)) != len(result) or tuple(sorted(result)) != result or any(x not in CANDIDATE_DURATIONS for x in result) or 30 not in result:
        raise ValueError("Studio lengths must be unique sorted candidates including 30")
    return result

def validate_duration(value, allowed=DEFAULT_DURATIONS):
    if type(value) is not int or value not in allowed:
        raise ValueError("Choose an available track length")
    return value

def child_duration(args):
    # Historical owner adapter invokes the same child without a duration argument.
    return validate_duration(int(args[4]) if len(args) > 4 else 30, CANDIDATE_DURATIONS)

def validate_media_probe(probe, requested):
    import math
    validate_duration(requested, CANDIDATE_DURATIONS)
    tracks = [x for x in probe.get("streams", []) if x.get("codec_type") == "audio"]
    actual = float(probe["format"]["duration"])
    if len(tracks) != 1 or tracks[0].get("codec_name") != "mp3" or not math.isfinite(actual) or abs(actual-requested) > 2:
        raise ValueError("Generated media codec/duration gate failed")
    return actual


def visible_durations(public_lengths, identity=None, owner_validation=False):
    # Only a server-validated owner session can see the candidate during verification.
    if owner_validation and identity and identity.get("role") == "owner":
        return tuple(sorted(set(public_lengths) | {120}))
    return public_lengths
