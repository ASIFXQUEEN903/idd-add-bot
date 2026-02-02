"""
FIXED PROFESSIONAL PYROGRAM ACCOUNT MANAGER
With session string error handling
"""

import os
import re
import asyncio
import logging
import threading
import time
import json
import hashlib
import secrets
import uuid
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from threading import RLock
from cryptography.fernet import Fernet
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, FloodWait, PhoneCodeEmpty, AuthKeyUnregistered,
    UserDeactivatedBan, UserDeactivated, SessionRevoked, AuthKeyDuplicated
)

# ========================
# CONFIGURATION
# ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========================
# SINGLE GLOBAL ASYNC MANAGER
# ========================
class GlobalAsyncManager:
    """Singleton with ONE global event loop for ALL Pyrogram clients"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_global_loop()
            return cls._instance
    
    def _init_global_loop(self):
        """Initialize single global event loop"""
        try:
            # Get existing loop or create new one
            self._loop = asyncio.get_event_loop()
            if self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        except RuntimeError:
            # No loop exists
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        
        self._loop.set_debug(False)
        logger.info(f"✅ Global event loop initialized: {self._loop}")
    
    def get_loop(self):
        """Get the global event loop"""
        return self._loop
    
    def run_async(self, coro, timeout: int = 30):
        """Run coroutine in sync context using global loop"""
        try:
            if self._loop.is_running():
                # Use run_coroutine_threadsafe
                future = asyncio.run_coroutine_threadsafe(coro, self._loop)
                return future.result(timeout=timeout)
            else:
                # Run directly in loop
                return self._loop.run_until_complete(coro)
        except asyncio.TimeoutError:
            logger.error(f"Async operation timed out after {timeout}s")
            raise TimeoutError(f"Operation timed out after {timeout} seconds")
        except Exception as e:
            logger.error(f"Async execution failed: {e}")
            raise
    
    def create_client(self, session_name: str, api_id: int, api_hash: str, **kwargs):
        """Create Pyrogram client bound to global loop"""
        # Remove in_memory from kwargs if already present
        kwargs.pop('in_memory', None)
        return Client(
            session_name,
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True,
            no_updates=True,
            **kwargs
        )

# Alias for compatibility
AsyncManager = GlobalAsyncManager

# ========================
# THREAD-SAFE SESSION STORAGE
# ========================
class ThreadSafeSessionStorage:
    """Thread-safe session storage with proper cleanup"""
    
    def __init__(self):
        self.login_sessions: Dict[str, Dict] = {}
        self.session_locks: Dict[str, RLock] = {}
        self.storage_lock = RLock()
        
    def create_session(self, phone: str, device_info: Dict, 
                      phone_code_hash: str) -> str:
        """Create unique session with extended lifetime (15 minutes)"""
        with self.storage_lock:
            # Generate unique session key
            session_id = f"{phone}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
            
            # Clean up any old sessions for this phone (prevent overwrite)
            self._cleanup_old_sessions_for_phone(phone)
            
            # Create new session
            self.login_sessions[session_id] = {
                "phone": phone,
                "device_info": device_info,
                "phone_code_hash": phone_code_hash,
                "created_at": time.time(),
                "expires_at": time.time() + 900,  # 15 minutes
                "status": "otp_sent",
                "client": None,
                "requires_2fa": None,
                "last_activity": time.time(),
                "verified": False,
                "user_id": None
            }
            
            # Create individual lock for this session
            self.session_locks[session_id] = RLock()
            
            logger.info(f"Created session {session_id[:12]}... for {phone}")
            return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session with validation"""
        with self.storage_lock:
            session = self.login_sessions.get(session_id)
            if not session:
                return None
            
            # Check expiration
            if time.time() > session["expires_at"]:
                logger.info(f"Session {session_id[:12]}... expired")
                self._remove_session(session_id)
                return None
            
            # Update last activity
            session["last_activity"] = time.time()
            return session
    
    def update_session(self, session_id: str, **updates):
        """Update session data with lock"""
        if session_id not in self.session_locks:
            return False
        
        with self.session_locks[session_id]:
            session = self.login_sessions.get(session_id)
            if not session:
                return False
            
            for key, value in updates.items():
                session[key] = value
            session["last_activity"] = time.time()
            return True
    
    def mark_verified(self, session_id: str, requires_2fa: bool = False):
        """Mark OTP as verified"""
        return self.update_session(
            session_id,
            status="otp_verified",
            verified=True,
            requires_2fa=requires_2fa
        )
    
    def set_client(self, session_id: str, client):
        """Store client in session"""
        return self.update_session(session_id, client=client)
    
    def get_client(self, session_id: str):
        """Get client from session"""
        session = self.get_session(session_id)
        return session.get("client") if session else None
    
    def mark_2fa_required(self, session_id: str):
        """Mark session as requiring 2FA"""
        return self.update_session(
            session_id,
            status="awaiting_2fa",
            requires_2fa=True
        )
    
    def complete_session(self, session_id: str):
        """Mark session as completed"""
        return self.update_session(session_id, status="completed")
    
    def _remove_session(self, session_id: str):
        """Remove session safely"""
        with self.storage_lock:
            if session_id in self.login_sessions:
                # Clean up client if exists
                session = self.login_sessions[session_id]
                if session.get("client"):
                    try:
                        # Schedule disconnect in background
                        client = session["client"]
                        if hasattr(client, 'is_connected') and client.is_connected:
                            asyncio.create_task(self._safe_disconnect(client))
                    except Exception as e:
                        logger.debug(f"Client cleanup error: {e}")
                
                del self.login_sessions[session_id]
                logger.info(f"Removed session {session_id[:12]}...")
            
            if session_id in self.session_locks:
                del self.session_locks[session_id]
    
    def _cleanup_old_sessions_for_phone(self, phone: str):
        """Clean up old sessions for specific phone"""
        with self.storage_lock:
            to_remove = []
            current_time = time.time()
            
            for session_id, session in self.login_sessions.items():
                if session["phone"] == phone:
                    # Remove if expired OR different active session exists
                    if (current_time > session["expires_at"] or 
                        session["status"] in ["completed", "failed"]):
                        to_remove.append(session_id)
            
            for session_id in to_remove:
                self._remove_session(session_id)
    
    def cleanup_expired_sessions(self):
        """Clean up all expired sessions (except active OTP/2FA)"""
        with self.storage_lock:
            current_time = time.time()
            to_remove = []
            
            for session_id, session in self.login_sessions.items():
                # Remove only if expired AND not in active state
                if (current_time > session["expires_at"] and 
                    session["status"] not in ["otp_sent", "awaiting_2fa", "otp_verified"]):
                    to_remove.append(session_id)
            
            for session_id in to_remove:
                self._remove_session(session_id)
            
            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} expired sessions")
    
    def get_stats(self):
        """Get session statistics"""
        with self.storage_lock:
            stats = {
                "total_sessions": len(self.login_sessions),
                "by_status": {},
                "by_phone": {}
            }
            
            for session in self.login_sessions.values():
                status = session.get("status", "unknown")
                phone = session.get("phone", "unknown")
                
                stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
                stats["by_phone"][phone] = stats["by_phone"].get(phone, 0) + 1
            
            return stats
    
    async def _safe_disconnect(self, client):
        """Safely disconnect client"""
        try:
            if client and hasattr(client, 'is_connected') and client.is_connected:
                await client.disconnect()
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.debug(f"Safe disconnect error: {e}")

# ========================
# ENCRYPTION MANAGER (WITH SESSION VALIDATION)
# ========================
class EncryptionManager:
    """Handle encryption/decryption of sensitive data with validation"""
    
    def __init__(self, encryption_key: Optional[str] = None):
        if encryption_key:
            self.key = encryption_key.encode()
        else:
            self.key = Fernet.generate_key()
        
        self.cipher = Fernet(self.key)
    
    def encrypt(self, data: str) -> str:
        """Encrypt sensitive data"""
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return data
    
    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt sensitive data with validation"""
        try:
            # First try to decrypt
            decrypted = self.cipher.decrypt(encrypted_data.encode()).decode()
            
            # Validate if it's a proper session string
            self._validate_session_string(decrypted)
            
            return decrypted
        except Exception as e:
            logger.warning(f"Decryption failed, trying as plaintext: {e}")
            # If decryption fails, try to validate as plaintext
            try:
                self._validate_session_string(encrypted_data)
                return encrypted_data
            except Exception as e2:
                logger.error(f"Invalid session string: {e2}")
                # Return empty string if invalid
                return ""
    
    def _validate_session_string(self, session_string: str):
        """Validate Telegram session string format"""
        if not session_string or len(session_string) < 100:
            raise ValueError("Session string too short")
        
        # Try to decode as base64
        try:
            # Check if it's base64 encoded
            decoded = base64.b64decode(session_string + '==')
            if len(decoded) < 100:
                raise ValueError("Decoded session too short")
        except:
            # If not base64, check if it's hex or string format
            if not re.match(r'^[A-Za-z0-9+/=]+$', session_string):
                raise ValueError("Invalid session string format")
    
    def get_key(self) -> str:
        """Get encryption key"""
        return self.key.decode()

# ========================
# DEVICE MANAGER
# ========================
class DeviceManager:
    """Manage device information for sessions"""
    
    DEVICE_PROFILES = [
        {"device_model": "Netflix Num Adding", "system_version": "Android 14", "app_version": "Telegram Android 10.8.0", "lang_code": "en"},
        {"device_model": "Netflix Num Adding 5", "system_version": "Android 14", "app_version": "Telegram Android 10.2.3", "lang_code": "en"},
        {"device_model": "Netflix Num Adding 3", "system_version": "iOS 17.2", "app_version": "Telegram iOS 10.5.1", "lang_code": "en"},
    ]
    
    @staticmethod
    def get_random_device() -> Dict[str, str]:
        """Get random device configuration"""
        import random
        return random.choice(DeviceManager.DEVICE_PROFILES)

# ========================
# ACCOUNT MANAGER (FIXED WITH SESSION VALIDATION)
# ========================
class ProfessionalAccountManager:
    """
    Fixed Account Manager with:
    - Session string validation
    - Better error handling
    - Proper OTP fetching
    """
    
    def __init__(self, api_id: int, api_hash: str, encryption_key: Optional[str] = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.async_manager = AsyncManager()
        self.session_storage = ThreadSafeSessionStorage()
        self.encryption = EncryptionManager(encryption_key)
        self.device_manager = DeviceManager()
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
        
        logger.info(f"✅ Account Manager initialized for API ID: {api_id}")
    
    # ========================
    # PUBLIC METHODS
    # ========================
    
    def send_otp(self, phone_number: str) -> Dict[str, Any]:
        """Send OTP to phone number"""
        return self.async_manager.run_async(self._send_otp_async(phone_number))
    
    def verify_otp(self, session_key: str, otp_code: str) -> Dict[str, Any]:
        """Verify OTP and handle 2FA transition"""
        return self.async_manager.run_async(self._verify_otp_async(session_key, otp_code))
    
    def verify_2fa(self, session_key: str, password: str) -> Dict[str, Any]:
        """Verify 2FA password"""
        return self.async_manager.run_async(self._verify_2fa_async(session_key, password))
    
    def get_latest_otp(self, session_string: str, phone: str) -> Optional[str]:
        """Manually fetch latest OTP from Telegram messages"""
        return self.async_manager.run_async(
            self._get_latest_otp_async(session_string, phone)
        )
    
    # ========================
    # ASYNC IMPLEMENTATIONS
    # ========================
    
    async def _send_otp_async(self, phone_number: str) -> Dict[str, Any]:
        """Send OTP with proper session management"""
        client = None
        
        try:
            # Get device configuration
            device = self.device_manager.get_random_device()
            
            # Create unique session name
            session_name = f"login_{phone_number}_{int(time.time())}"
            
            # Create client with device info
            client = self.async_manager.create_client(
                session_name,
                self.api_id,
                self.api_hash,
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
                lang_code=device["lang_code"]
            )
            
            # Connect and send code
            await client.connect()
            
            # Check if already authorized
            try:
                if await client.get_me():
                    await client.disconnect()
                    return {
                        "success": False,
                        "error": "Already authorized",
                        "error_code": "ALREADY_AUTHORIZED"
                    }
            except:
                pass
            
            # Send verification code
            sent_code = await client.send_code(phone_number)
            
            # Create session in storage
            session_key = self.session_storage.create_session(
                phone_number,
                device,
                sent_code.phone_code_hash
            )
            
            # Store client in session
            self.session_storage.set_client(session_key, client)
            
            logger.info(f"OTP sent to {phone_number}")
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "session_key": session_key,
                "device_info": device,
                "expires_in": 900  # 15 minutes
            }
            
        except FloodWait as e:
            wait_time = e.value
            logger.warning(f"Flood wait: {wait_time} seconds")
            
            if client:
                await client.disconnect()
            
            return {
                "success": False,
                "error": f"Please wait {wait_time} seconds",
                "error_code": "FLOOD_WAIT",
                "wait_time": wait_time
            }
            
        except PhoneNumberInvalid:
            logger.error(f"Invalid phone number: {phone_number}")
            return {
                "success": False,
                "error": "Invalid phone number",
                "error_code": "INVALID_PHONE"
            }
            
        except Exception as e:
            logger.error(f"Send OTP error: {e}", exc_info=True)
            
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            
            return {
                "success": False,
                "error": "Failed to send OTP",
                "error_code": "SEND_FAILED"
            }
    
    async def _verify_otp_async(self, session_key: str, otp_code: str) -> Dict[str, Any]:
        """Verify OTP with proper 2FA detection"""
        try:
            # Get session
            session = self.session_storage.get_session(session_key)
            if not session:
                return {
                    "success": False,
                    "error": "Session expired",
                    "error_code": "SESSION_EXPIRED"
                }
            
            phone = session["phone"]
            phone_code_hash = session["phone_code_hash"]
            client = session.get("client")
            
            if not client:
                return {
                    "success": False,
                    "error": "Session error",
                    "error_code": "SESSION_ERROR"
                }
            
            # Verify OTP
            try:
                await client.sign_in(
                    phone_number=phone,
                    phone_code=otp_code,
                    phone_code_hash=phone_code_hash
                )
                
                # OTP successful, check if 2FA is needed
                try:
                    # This will trigger SessionPasswordNeeded if 2FA is enabled
                    await client.get_me()
                    
                    # No 2FA required
                    session_string = await client.export_session_string()
                    self.session_storage.mark_verified(session_key, requires_2fa=False)
                    self.session_storage.complete_session(session_key)
                    
                    # Get account info
                    me = await client.get_me()
                    account_info = {
                        "user_id": me.id,
                        "first_name": me.first_name,
                        "last_name": me.last_name,
                        "username": me.username,
                        "phone_number": me.phone_number,
                    }
                    
                    await client.disconnect()
                    
                    return {
                        "success": True,
                        "session_string": self.encryption.encrypt(session_string),
                        "has_2fa": False,
                        "account_info": account_info
                    }
                    
                except SessionPasswordNeeded:
                    # 2FA required - KEEP CLIENT CONNECTED
                    self.session_storage.mark_verified(session_key, requires_2fa=True)
                    self.session_storage.mark_2fa_required(session_key)
                    
                    return {
                        "success": True,
                        "needs_2fa": True,
                        "session_key": session_key
                    }
                    
            except SessionPasswordNeeded:
                # 2FA required (caught during sign_in)
                self.session_storage.mark_verified(session_key, requires_2fa=True)
                self.session_storage.mark_2fa_required(session_key)
                
                return {
                    "success": True,
                    "needs_2fa": True,
                    "session_key": session_key
                }
                
            except (PhoneCodeInvalid, PhoneCodeExpired) as e:
                error_type = type(e).__name__
                return {
                    "success": False,
                    "error": f"Invalid OTP code",
                    "error_code": error_type.upper()
                }
                
        except Exception as e:
            logger.error(f"Verify OTP error: {e}", exc_info=True)
            return {
                "success": False,
                "error": "Verification failed",
                "error_code": "VERIFICATION_FAILED"
            }
    
    async def _verify_2fa_async(self, session_key: str, password: str) -> Dict[str, Any]:
        """Verify 2FA password"""
        try:
            session = self.session_storage.get_session(session_key)
            if not session or not session.get("requires_2fa"):
                return {
                    "success": False,
                    "error": "Invalid session",
                    "error_code": "INVALID_SESSION"
                }
            
            client = session.get("client")
            if not client:
                return {
                    "success": False,
                    "error": "Session error",
                    "error_code": "SESSION_ERROR"
                }
            
            # Verify 2FA password
            try:
                await client.check_password(password)
                
                # Get session string
                session_string = await client.export_session_string()
                
                # Get account info
                me = await client.get_me()
                account_info = {
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone_number": me.phone_number,
                }
                
                self.session_storage.complete_session(session_key)
                await client.disconnect()
                
                return {
                    "success": True,
                    "session_string": self.encryption.encrypt(session_string),
                    "has_2fa": True,
                    "account_info": account_info
                }
                
            except Exception as e:
                logger.error(f"2FA password error: {e}")
                return {
                    "success": False,
                    "error": "Invalid 2FA password",
                    "error_code": "INVALID_2FA"
                }
                
        except Exception as e:
            logger.error(f"2FA verification error: {e}", exc_info=True)
            return {
                "success": False,
                "error": "2FA verification failed",
                "error_code": "2FA_FAILED"
            }
    
    async def _get_latest_otp_async(self, session_string: str, phone: str):
        """Async function to fetch OTP with session validation"""
        client = None
        try:
            # Decrypt if encrypted with validation
            decrypted_session = self.encryption.decrypt(session_string)
            
            # Check if session string is valid
            if not decrypted_session or len(decrypted_session) < 100:
                logger.error(f"Invalid session string for {phone}")
                return None
            
            # Create client from session string with try-except
            try:
                client = Client(
                    name=f"otp_fetch_{int(datetime.now().timestamp())}",
                    session_string=decrypted_session,
                    api_id=self.api_id,
                    api_hash=self.api_hash,
                    in_memory=True,
                    no_updates=True
                )
                
                await client.connect()
                
                # Try to get user info to verify session is valid
                try:
                    me = await client.get_me()
                    if not me:
                        logger.error(f"Session invalid for {phone}")
                        await client.disconnect()
                        return None
                except Exception as e:
                    logger.error(f"Session validation failed for {phone}: {e}")
                    await client.disconnect()
                    return None
                
                latest_otp = None
                latest_time = None
                
                # Search in Telegram chat (Official Telegram messages)
                try:
                    async for message in client.get_chat_history("Telegram", limit=30):
                        if message.text and "code" in message.text.lower():
                            # Look for 5-digit OTP
                            otp_matches = re.findall(r'\b\d{5}\b', message.text)
                            for otp in otp_matches:
                                message_time = message.date.timestamp() if message.date else 0
                                if latest_time is None or message_time > latest_time:
                                    latest_time = message_time
                                    latest_otp = otp
                                    logger.info(f"Found OTP in Telegram chat: {latest_otp}")
                            
                            # If no 5-digit, look for 6-digit
                            if not latest_otp:
                                otp_matches = re.findall(r'\b\d{6}\b', message.text)
                                for otp in otp_matches:
                                    message_time = message.date.timestamp() if message.date else 0
                                    if latest_time is None or message_time > latest_time:
                                        latest_time = message_time
                                        latest_otp = otp
                                        logger.info(f"Found 6-digit OTP in Telegram chat: {latest_otp}")
                except Exception as e:
                    logger.warning(f"Error searching Telegram chat: {e}")
                
                # Search in 777000 (Telegram system notifications)
                if not latest_otp:
                    try:
                        async for message in client.get_chat_history(777000, limit=30):
                            if message.text and ("code" in message.text.lower() or "verify" in message.text.lower()):
                                otp_matches = re.findall(r'\b\d{5}\b', message.text)
                                for otp in otp_matches:
                                    message_time = message.date.timestamp() if message.date else 0
                                    if latest_time is None or message_time > latest_time:
                                        latest_time = message_time
                                        latest_otp = otp
                                        logger.info(f"Found OTP in 777000: {latest_otp}")
                                
                                if not latest_otp:
                                    otp_matches = re.findall(r'\b\d{6}\b', message.text)
                                    for otp in otp_matches:
                                        message_time = message.date.timestamp() if message.date else 0
                                        if latest_time is None or message_time > latest_time:
                                            latest_time = message_time
                                            latest_otp = otp
                                            logger.info(f"Found 6-digit OTP in 777000: {latest_otp}")
                    except Exception as e:
                        logger.warning(f"Error searching 777000: {e}")
                
                await client.disconnect()
                
                if latest_otp:
                    logger.info(f"✅ OTP found for {phone}: {latest_otp}")
                else:
                    logger.info(f"❌ No OTP found for {phone}")
                
                return latest_otp
                
            except Exception as e:
                logger.error(f"Client creation/connection error for {phone}: {e}")
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
                return None
                
        except Exception as e:
            logger.error(f"Get OTP error for {phone}: {e}")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return None
    
    async def _safe_disconnect(self, client):
        """Safely disconnect client"""
        try:
            if client and hasattr(client, 'is_connected') and client.is_connected:
                await client.disconnect()
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.debug(f"Safe disconnect error: {e}")
    
    def _cleanup_loop(self):
        """Background cleanup of expired sessions"""
        while True:
            try:
                time.sleep(60)  # Run every minute
                self.session_storage.cleanup_expired_sessions()
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                time.sleep(10)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics"""
        session_stats = self.session_storage.get_stats()
        
        return {
            "session_storage": session_stats,
            "encryption_enabled": hasattr(self.encryption, 'cipher')
        }
    
    def disconnect_all(self):
        """Disconnect all clients"""
        # Session storage will handle disconnection during cleanup
        self.session_storage.cleanup_expired_sessions()


# ========================
# FACTORY FUNCTION
# ========================
def create_account_manager(api_id: int, api_hash: str, 
                          encryption_key: Optional[str] = None) -> ProfessionalAccountManager:
    """Factory function to create Account Manager"""
    return ProfessionalAccountManager(api_id, api_hash, encryption_key)
