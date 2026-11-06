import re

from django import forms

MAX_LOGO_BYTES = 512 * 1024
_SVG_START = re.compile(
    r"\A\s*(?:<\?xml[^>]*>\s*)?(?:<!--.*?-->\s*)*(?:<!DOCTYPE[^>]*>\s*)?"
    r"(?:<!--.*?-->\s*)*<svg[\s>]",
    re.S | re.I,
)


def logo_type(data: bytes) -> str:
    """Identify a logo from its bytes. The uploaded name and type are not trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ""
    return "image/svg+xml" if _SVG_START.match(text) else ""


class AccessCodeForm(forms.Form):
    code = forms.CharField(max_length=20)

    def clean_code(self):
        code = re.sub(r"[\s-]", "", self.cleaned_data["code"])
        if not re.fullmatch(r"[0-9]{9}", code):
            raise forms.ValidationError("Enter the 9-digit code from your technician.")
        return code


class CreateAccessCodeForm(forms.Form):
    site = forms.IntegerField(min_value=1)
    label = forms.CharField(max_length=100, required=False)
    lifetime = forms.TypedChoiceField(
        choices=[
            (15, "15 minutes"),
            (30, "30 minutes"),
            (60, "1 hour"),
            (240, "4 hours"),
        ],
        coerce=int,
        initial=30,
    )
    max_downloads = forms.IntegerField(min_value=1, max_value=20, initial=5)


class BrandingForm(forms.Form):
    name = forms.CharField(max_length=100, required=False)
    contact_email = forms.EmailField(required=False)
    contact_phone = forms.CharField(max_length=40, required=False)
    logo = forms.FileField(required=False)
    remove_logo = forms.BooleanField(required=False)

    def clean_logo(self):
        upload = self.cleaned_data.get("logo")
        if not upload:
            return None
        if upload.size > MAX_LOGO_BYTES:
            raise forms.ValidationError("Use an image under 512 KB.")
        data = upload.read()
        kind = logo_type(data)
        if not kind:
            raise forms.ValidationError("Use a PNG, JPEG, WebP or SVG image.")
        return data, kind
