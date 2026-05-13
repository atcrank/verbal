"""
URL configuration for verbal_config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import os
from django.contrib import admin
from django.urls import path, include, re_path
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.static import serve
from django.conf import settings
from ninja import NinjaAPI
from ninja.security import django_auth
from llm_api.api import router as llm_router
from metacognition import api as metacognition_api
api = NinjaAPI(auth=django_auth)

api.add_router("/llm/", llm_router)
api.add_router("/meta/", metacognition_api.router)
@api.post("/csrf")
@ensure_csrf_cookie
@csrf_exempt
def get_csrf_token(request):
    return HttpResponse()

from django.conf import settings
from ninja.security import django_auth
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator

from django.contrib.auth.forms import (
    PasswordResetForm,
    SetPasswordForm,
    PasswordChangeForm
)

from django.contrib.auth import (
    login as django_login,
    logout as django_logout,
    authenticate
)

from .schema import (
    UserOut,
    LoginIn,
    RequestPasswordResetIn,
    SetPasswordIn,
    ChangePasswordIn,
    ErrorsOut
)


_TGS = ['Django Ninja Auth']
_LOGIN_BACKEND = 'django.contrib.auth.backends.ModelBackend'


@api.post('/', tags=_TGS, response={200: UserOut, 403: None}, auth=None)
def login(request, data: LoginIn):
    user = authenticate(backend=_LOGIN_BACKEND, **data.dict())
    if user is not None and user.is_active:
        django_login(request, user, backend=_LOGIN_BACKEND)
        return user
    return 403, None


@api.delete('/', tags=_TGS, response={204: None}, auth=django_auth)
def logout(request):
    django_logout(request)
    return 204, None


@api.get('/me', tags=_TGS, response=UserOut, auth=django_auth)
def me(request):
    return request.user


@api.post('/request_password_reset', tags=_TGS,  response={204: None}, auth=None)
def request_password_reset(request, data: RequestPasswordResetIn):
    form = PasswordResetForm(data.dict())
    if form.is_valid():
        form.save(
            request=request,
            extra_email_context=(
                {'frontend_url': settings.FRONTEND_URL} if
                hasattr(settings, 'FRONTEND_URL') else None
            ),
        )
    return 204, None

@api.post('/reset_password', tags=_TGS, response={200: UserOut, 403: ErrorsOut, 422: None}, auth=None)
def reset_password(request, data: SetPasswordIn):
    user_field = get_user_model().USERNAME_FIELD
    user_data = {user_field: getattr(data, user_field)}
    user = get_user_model().objects.filter(**user_data)

    if user.exists():
        user = user.get()
        if default_token_generator.check_token(user, data.token):
            form = SetPasswordForm(user, data.dict())
            if form.is_valid():
                form.save()
                django_login(request, user, backend=_LOGIN_BACKEND)
                return user
            return 403, {'errors': dict(form.errors)}
    return 422, None


@api.post(    '/change_password', tags=_TGS, response={200: None, 403: ErrorsOut}, auth=django_auth)
def change_password(request, data: ChangePasswordIn):
    form = PasswordChangeForm(request.user, data.dict())
    if form.is_valid():
        form.save()
        update_session_auth_hash(request, request.user)
        return 200
    return 403, {'errors': dict(form.errors)}


DOCS_DIR = os.path.join(settings.BASE_DIR, 'documentation', 'build', 'html')

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("benchmarking/", include("benchmarking.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    
    # Serve Sphinx Documentation
    re_path(r'^docs/(?P<path>.*)$', serve, {'document_root': DOCS_DIR}),
    path('docs/', serve, {'document_root': DOCS_DIR, 'path': 'index.html'}),
]
