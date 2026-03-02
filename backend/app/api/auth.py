"""
AWS Cognito Auth — Sign up, confirm, sign in, refresh, sign out
"""
import uuid
import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from app.core.config import settings

router = APIRouter()


def _cognito():
    return boto3.client(
        "cognito-idp",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def _require_cognito():
    if not settings.COGNITO_CLIENT_ID or not settings.COGNITO_USER_POOL_ID:
        raise HTTPException(503, "Auth not configured — add COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID to .env")


# ── Models ────────────────────────────────────────────────────────────────────

class SignUpRequest(BaseModel):
    name: str
    email: str
    password: str


class ConfirmRequest(BaseModel):
    email: str
    code: str


class SignInRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


class AuthResponse(BaseModel):
    access_token: str
    id_token: str
    refresh_token: str
    expires_in: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/signup", status_code=201)
async def sign_up(req: SignUpRequest):
    """Register new user. Cognito will send a verification email."""
    _require_cognito()
    try:
        _cognito().sign_up(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=str(uuid.uuid4()),
            Password=req.password,
            UserAttributes=[
                {"Name": "email", "Value": req.email},
                {"Name": "name", "Value": req.name},
            ],
        )
        return {"message": "Account created. Check your email for a verification code."}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg = e.response["Error"]["Message"]
        if code == "UsernameExistsException":
            raise HTTPException(409, "An account with this email already exists")
        if code == "InvalidPasswordException":
            raise HTTPException(400, msg)
        if code == "InvalidParameterException":
            raise HTTPException(400, msg)
        raise HTTPException(500, f"Sign up failed: {code}")


@router.post("/confirm")
async def confirm_sign_up(req: ConfirmRequest):
    """Confirm account with the code sent to email."""
    _require_cognito()
    try:
        username = _resolve_username_unconfirmed(req.email)
        _cognito().confirm_sign_up(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=username,
            ConfirmationCode=req.code,
        )
        return {"message": "Email confirmed. You can now sign in."}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "CodeMismatchException":
            raise HTTPException(400, "Incorrect verification code")
        if code == "ExpiredCodeException":
            raise HTTPException(400, "Verification code has expired — request a new one")
        if code == "NotAuthorizedException":
            raise HTTPException(400, "Account is already confirmed")
        raise HTTPException(500, f"Confirmation failed: {code}")


@router.post("/resend-code")
async def resend_confirmation_code(email: str):
    """Resend email verification code."""
    _require_cognito()
    try:
        username = _resolve_username_unconfirmed(email)
        _cognito().resend_confirmation_code(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=username,
        )
        return {"message": "Verification code resent"}
    except ClientError as e:
        raise HTTPException(400, e.response["Error"]["Message"])


def _resolve_username_unconfirmed(email: str) -> str:
    """Look up username for any user (including unconfirmed) by email."""
    resp = _cognito().list_users(
        UserPoolId=settings.COGNITO_USER_POOL_ID,
        Filter=f'email = "{email}"',
        Limit=1,
    )
    users = resp.get("Users", [])
    if not users:
        raise HTTPException(400, "No account found for this email")
    return users[0]["Username"]


def _resolve_username(email: str) -> str:
    """Look up the internal Cognito username for a confirmed user by email."""
    resp = _cognito().list_users(
        UserPoolId=settings.COGNITO_USER_POOL_ID,
        Filter=f'email = "{email}"',
        Limit=1,
    )
    users = resp.get("Users", [])
    if not users:
        raise HTTPException(401, "Incorrect email or password")
    username = users[0]["Username"]
    print(f"[signin] resolved username for {email!r} => {username!r}")
    return username


@router.post("/signin", response_model=AuthResponse)
async def sign_in(req: SignInRequest):
    """Authenticate and return JWT tokens."""
    _require_cognito()
    try:
        username = _resolve_username(req.email)
        resp = _cognito().admin_initiate_auth(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            ClientId=settings.COGNITO_CLIENT_ID,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": req.password,
            },
        )
        result = resp["AuthenticationResult"]
        return AuthResponse(
            access_token=result["AccessToken"],
            id_token=result["IdToken"],
            refresh_token=result["RefreshToken"],
            expires_in=result["ExpiresIn"],
        )
    except HTTPException:
        raise
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            raise HTTPException(401, "Incorrect email or password")
        if code == "UserNotConfirmedException":
            raise HTTPException(403, "Email not confirmed — check your inbox")
        if code == "PasswordResetRequiredException":
            raise HTTPException(403, "Password reset required")
        raise HTTPException(500, f"Sign in failed: {code}")


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(req: RefreshRequest):
    """Exchange refresh token for new access + id tokens."""
    _require_cognito()
    try:
        resp = _cognito().admin_initiate_auth(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            ClientId=settings.COGNITO_CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": req.refresh_token},
        )
        result = resp["AuthenticationResult"]
        return AuthResponse(
            access_token=result["AccessToken"],
            id_token=result["IdToken"],
            refresh_token=req.refresh_token,  # Cognito doesn't return a new refresh token
            expires_in=result["ExpiresIn"],
        )
    except ClientError as e:
        raise HTTPException(401, "Invalid or expired refresh token")


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    """Send a password reset code to the user's email."""
    _require_cognito()
    try:
        username = _resolve_username_unconfirmed(req.email)
        _cognito().forgot_password(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=username,
        )
        return {"message": "Reset code sent. Check your email."}
    except HTTPException:
        raise
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "UserNotFoundException":
            raise HTTPException(404, "No account found for this email")
        if code == "LimitExceededException":
            raise HTTPException(429, "Too many attempts. Please wait before trying again.")
        raise HTTPException(500, f"Failed to send reset code: {code}")


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    """Confirm the reset code and set a new password."""
    _require_cognito()
    try:
        username = _resolve_username_unconfirmed(req.email)
        _cognito().confirm_forgot_password(
            ClientId=settings.COGNITO_CLIENT_ID,
            Username=username,
            ConfirmationCode=req.code,
            Password=req.new_password,
        )
        return {"message": "Password reset successfully. You can now sign in."}
    except HTTPException:
        raise
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "CodeMismatchException":
            raise HTTPException(400, "Incorrect reset code")
        if code == "ExpiredCodeException":
            raise HTTPException(400, "Reset code has expired — request a new one")
        if code == "InvalidPasswordException":
            raise HTTPException(400, e.response["Error"]["Message"])
        raise HTTPException(500, f"Reset failed: {code}")


@router.post("/signout")
async def sign_out(access_token: str):
    """Revoke all tokens for the user."""
    _require_cognito()
    try:
        _cognito().global_sign_out(AccessToken=access_token)
        return {"message": "Signed out successfully"}
    except ClientError:
        pass  # Token may already be expired — treat as success
    return {"message": "Signed out"}
