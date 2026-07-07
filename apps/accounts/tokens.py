from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

User = get_user_model()


def generate_activation_token(user) -> tuple[str, str]:
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uidb64, token


def validate_activation_token(uidb64: str, token: str) -> int | None:
    try:
        pk = int(urlsafe_base64_decode(uidb64).decode())
        user = User.objects.get(pk=pk)
        if default_token_generator.check_token(user, token):
            return pk
        return None
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return None
