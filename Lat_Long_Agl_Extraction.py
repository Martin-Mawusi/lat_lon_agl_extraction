# AGL (Above Ground Level) from an image
from PIL import Image
import piexif
import math
import re
from lxml import etree
import srtm  # SRTM DEM for ground elevation (meters, approx MSL)

# Making these variables callable outside the functions
LAST_LAT = None
LAST_LON = None
print("hello")
def _rational_to_float(val):
    try:
        if hasattr(val, 'numerator') and hasattr(val, 'denominator'):
            return float(val.numerator) / float(val.denominator or 1.0)
        if isinstance(val, tuple) and len(val) == 2:
            num, den = val
            return float(num) / float(den or 1.0)
        return float(val)
    except Exception:
        return None

def _dms_to_decimal(dms, ref):
    if dms is None:
        return None
    try:
        d, m, s = dms
        d = _rational_to_float(d)
        m = _rational_to_float(m)
        s = _rational_to_float(s)
        dec = d + (m/60.0) + (s/3600.0)
        if ref in ['S', 'W']:
            dec = -dec
        return dec
    except Exception:
        try:
            dec = float(dms)
            if ref in ['S', 'W']:
                dec = -dec
            return dec
        except Exception:
            return None

def _extract_exif_gps(image_path):
    exif_dict = piexif.load(image_path)
    gps_ifd = exif_dict.get("GPS", {}) or {}
    lat = lon = None
    if piexif.GPSIFD.GPSLatitude in gps_ifd and piexif.GPSIFD.GPSLatitudeRef in gps_ifd:
        lat = _dms_to_decimal(
            gps_ifd[piexif.GPSIFD.GPSLatitude],
            gps_ifd[piexif.GPSIFD.GPSLatitudeRef].decode(errors='ignore') if isinstance(gps_ifd[piexif.GPSIFD.GPSLatitudeRef], bytes) else gps_ifd[piexif.GPSIFD.GPSLatitudeRef]
        )
    if piexif.GPSIFD.GPSLongitude in gps_ifd and piexif.GPSIFD.GPSLongitudeRef in gps_ifd:
        lon = _dms_to_decimal(
            gps_ifd[piexif.GPSIFD.GPSLongitude],
            gps_ifd[piexif.GPSIFD.GPSLongitudeRef].decode(errors='ignore') if isinstance(gps_ifd[piexif.GPSIFD.GPSLongitudeRef], bytes) else gps_ifd[piexif.GPSIFD.GPSLongitudeRef]
        )

    alt_m_msl = None
    alt_ref = gps_ifd.get(piexif.GPSIFD.GPSAltitudeRef)
    if piexif.GPSIFD.GPSAltitude in gps_ifd:
        alt_m = _rational_to_float(gps_ifd[piexif.GPSIFD.GPSAltitude])
        if alt_m is not None:
            if alt_ref == 1:
                alt_m = -alt_m
            alt_m_msl = alt_m

    return {
        'lat': lat,
        'lon': lon,
        'alt_m_msl': alt_m_msl,
        'alt_ref': alt_ref if isinstance(alt_ref, int) else (alt_ref[0] if isinstance(alt_ref, (bytes, bytearray)) and len(alt_ref)>0 else alt_ref)
    }

def _extract_xmp_fields(image_path):
    rel_alt = abs_alt = None
    with open(image_path, 'rb') as f:
        data = f.read()
    m = re.search(br'<x:xmpmeta[^>]*>.*?</x:xmpmeta>', data, flags=re.DOTALL)
    if not m:
        return {'relative_alt_m': None, 'absolute_alt_m': None}
    xmp_bytes = m.group(0)
    try:
        root = etree.fromstring(xmp_bytes)
        text_content = b" ".join(root.itertext()).decode(errors='ignore')
        rel_match = re.search(r'RelativeAltitude\s*([:=])\s*([-+]?\d+(\.\d+)?)', text_content)
        abs_match = re.search(r'AbsoluteAltitude\s*([:=])\s*([-+]?\d+(\.\d+)?)', text_content)
        if rel_match:
            rel_alt = float(rel_match.group(2))
        if abs_match:
            abs_alt = float(abs_match.group(2))
        if rel_alt is None or abs_alt is None:
            for elem in root.iter():
                for k, v in elem.attrib.items():
                    if 'RelativeAltitude' in k and rel_alt is None:
                        try: rel_alt = float(v)
                        except: pass
                    if 'AbsoluteAltitude' in k and abs_alt is None:
                        try: abs_alt = float(v)
                        except: pass
    except Exception:
        pass
    return {'relative_alt_m': rel_alt, 'absolute_alt_m': abs_alt}

def _ground_elevation_msl(lat, lon):
    try:
        data = srtm.get_data()
        h = data.get_elevation(lat, lon)
        return float(h) if h is not None else None
    except Exception:
        return None

def compute_agl(image_path, verbose=True):
    """
    Returns: (agl_meters, lat, lon)
    Also updates module-level LAST_LAT / LAST_LON for access anywhere.
    """
    global LAST_LAT, LAST_LON

    # Try DJI XMP relative altitude (already AGL)
    xmp = _extract_xmp_fields(image_path)
    if verbose:
        print("XMP fields:", xmp)

    # Regardless of AGL source, try to fetch lat/lon for external use
    gps = _extract_exif_gps(image_path)
    if verbose:
        print("EXIF GPS:", gps)
    LAST_LAT, LAST_LON = gps.get('lat'), gps.get('lon')

    if xmp.get('relative_alt_m') is not None:
        agl = float(xmp['relative_alt_m'])
        if verbose:
            print(f"[AGL] Using DJI XMP RelativeAltitude: {agl:.2f} m")
        return agl, LAST_LAT, LAST_LON

    # Fallback: EXIF GPS + SRTM DEM
    alt_msl = gps.get('alt_m_msl')
    if LAST_LAT is None or LAST_LON is None:
        raise ValueError("No GPS lat/lon found. Cannot estimate ground elevation for AGL.")
    if alt_msl is None:
        raise ValueError("No EXIF GPSAltitude (MSL) found. Cannot compute AGL without MSL altitude.")

    ground_msl = _ground_elevation_msl(LAST_LAT, LAST_LON)
    if ground_msl is None:
        raise ValueError("Ground elevation unavailable at this location (SRTM).")

    agl = alt_msl - ground_msl
    if verbose:
        print(f"[AGL] Using EXIF GPSAltitude(MSL) - SRTM Ground(MSL) = {alt_msl:.2f} - {ground_msl:.2f} = {agl:.2f} m")
    return agl, LAST_LAT, LAST_LON

#calling later
def get_last_lat_lon():
    """Returns the most recently parsed (lat, lon) or (None, None)."""
    return LAST_LAT, LAST_LON


img_path = 'DOF/bsg5.JPG'
agl_m, lat, lon = compute_agl(img_path, verbose=True)
print("AGL (m):", agl_m)
print("Lat:", lat, "Lon:", lon)

# lat2, lon2 = get_last_lat_lon()
# print(lat2, lon2)
