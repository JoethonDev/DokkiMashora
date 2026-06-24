import polib
po = polib.pofile("locale/ar/LC_MESSAGES/django.po")
for e in po:
    if e.msgid and "Course versions" in e.msgid:
        cv = "Course versions"
        print(f"msgid: {e.msgid!r}")
        print(f"msgid == cv: {e.msgid == cv}")
        print(f"msgid hex: {e.msgid.encode('utf-8').hex()}")
        print(f"cv hex:    {cv.encode('utf-8').hex()}")
        break
