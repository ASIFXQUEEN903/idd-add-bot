"""
FIXED ACCOUNT MANAGER - COMPACT VERSION
Fixes "Code expired" and async issues
"""

import os
import re
import asyncio
import logging
import threading
import time
import hashlib
import secrets
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from cryptography.fernet import Fernet
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, FloodWait, AuthKeyUnregistered
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
# ENCRYPTION MANAGER
# ========================
class EncryptionManager:
    def __init__(self, encryption_key: Optional[str] = None):
        self.key = encryption_key.encode() if encryption_key else Fernet.generate_key()
        self.cipher = Fernet(self.key)
    
    def encrypt(self, data: str) -> str:
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except:
            return data
    
    def decrypt(self, encrypted_data: str) -> str:
        try:
            return self.cipher.decrypt(encrypted_data.encode()).decode()
        except:
            return encrypted_data

# ========================
# RATE LIMITER
# ========================
class RateLimiter:
    def __init__(self):
        self.attempts: Dict[str, List[float]] = {}
        self.lock = threading.Lock()
    
    def check_limit(self, key: str, max_attempts: int = 3, window: int = 3600) -> Tuple[bool, Optional[int]]:
        with self.lock:
            current_time = time.time()
            
            if key in self.attempts:
                self.attempts[key] = [t for t in self.attempts[key] if current_time - t < window]
            
            attempts = self.attempts.get(key, [])
            if len(attempts) >= max_attempts:
                wait_time = int(window - (current_time - attempts[0]))
                return False, wait_time
            
            if key not in self.attempts:
                self.attempts[key] = []
            self.attempts[key].append(current_time)
            return True, None

# ========================
# DEVICE MANAGER
# ========================
class DeviceManager:
    DEVICE_PROFILES = [
        {"device_model": "iPhone 14 Pro", "system_version": "iOS 16.6", "app_version": "Telegram iOS 9.8.2", "lang_code": "en"},
        {"device_model": "Samsung Galaxy S23", "system_version": "Android 14", "app_version": "Telegram Android 10.8.0", "lang_code": "en"},
        {"device_model": "Desktop", "system_version": "Windows 11", "app_version": "Telegram Desktop 4.9.1", "lang_code": "en"}
    ]
    
    @staticmethod
    def get_random_device() -> Dict[str, str]:
        import random
        return random.choice(DeviceManager.DEVICE_PROFILES)

# ========================
# ACCOUNT MANAGER (FIXED)
# ========================
class ProfessionalAccountManager:
    """
    FIXED VERSION - No async loop issues
    """
    
    def __init__(self, api_id: int, api_hash: str, encryption_key: Optional[str] = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.encryption = EncryptionManager(encryption_key)
        self.rate_limiter = RateLimiter()
        self.device_manager = DeviceManager()
        
        # Simple session storage
        self.login_sessions: Dict[str, Dict] = {}
        self.sessions_lock = threading.Lock()
        
        # Statistics
        self.stats = {
            "logins_attempted": 0,
            "logins_successful": 0,
            "logins_failed": 0,
            "start_time": time.time()
        }
    
    # ========================
    # PUBLIC METHODS
    # ========================
    
    def send_otp(self, phone_number: str, ip_address: str = "unknown") -> Dict[str, Any]:
        """Send OTP - Thread-safe"""
        try:
            # Rate limiting
            allowed, wait_time = self.rate_limiter.check_limit(f"phone_{phone_number}")
            if not allowed:
                return {
                    "success": False,
                    "error": f"Wait {wait_time} seconds.",
                    "error_code": "RATE_LIMITED"
                }
            
            # Run async
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._send_otp_async(phone_number))
            loop.close()
            
            return result
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def verify_otp(self, session_key: str, otp_code: str, 
                   phone_number: str, phone_code_hash: str) -> Dict[str, Any]:
        """Verify OTP - Thread-safe"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                self._verify_otp_async(session_key, otp_code, phone_number, phone_code_hash)
            )
            loop.close()
            return result
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def verify_2fa(self, session_key: str, password: str) -> Dict[str, Any]:
        """Verify 2FA - Thread-safe"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._verify_2fa_async(session_key, password))
            loop.close()
            return result
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_latest_otp(self, session_string: str, phone: str) -> Optional[str]:
        """Get OTP - Thread-safe"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._get_latest_otp_async(session_string, phone))
            loop.close()
            return result
            
        except Exception:
            return None
    
    # ========================
    # ASYNC IMPLEMENTATIONS
    # ========================
    
    async def _send_otp_async(self, phone_number: str) -> Dict[str, Any]:
        """Send OTP"""
        client = None
        try:
            self.stats["logins_attempted"] += 1
            
            # Get device
            device = self.device_manager.get_random_device()
            
            # Create client
            timestamp = int(time.time())
            session_name = f"login_{phone_number}_{timestamp}"
            
            client = Client(
                name=session_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
                lang_code=device["lang_code"],
                in_memory=True,
                no_updates=True
            )
            
            # Connect and send OTP
            await client.connect()
            sent_code = await client.send_code(phone_number)
            
            # Generate session key
            session_key = hashlib.sha256(
                f"{phone_number}_{timestamp}_{secrets.token_hex(8)}".encode()
            ).hexdigest()[:32]
            
            # Store session info (NOT the client)
            with self.sessions_lock:
                self.login_sessions[session_key] = {
                    "phone": phone_number,
                    "device": device,
                    "phone_code_hash": sent_code.phone_code_hash,
                    "timestamp": timestamp,
                    "expires_at": timestamp + 300  # 5 minutes
                }
            
            # Disconnect client (we'll recreate it later)
            await client.disconnect()
            
            logger.info(f"OTP sent to {phone_number}")
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "session_key": session_key,
                "device_info": device,
                "expires_in": 300
            }
            
        except FloodWait as e:
            if client:
                await client.disconnect()
            
            logger.warning(f"Flood wait: {e.value} seconds")
            return {
                "success": False,
                "error": f"Wait {e.value} seconds.",
                "error_code": "FLOOD_WAIT"
            }
            
        except PhoneNumberInvalid:
            self.stats["logins_failed"] += 1
            return {
                "success": False,
                "error": "Invalid phone number",
                "error_code": "INVALID_PHONE"
            }
            
        except Exception as e:
            self.stats["logins_failed"] += 1
            logger.error(f"Send OTP error: {e}")
            
            if client:
                await client.disconnect()
            
            return {
                "success": False,
                "error": str(e),
                "error_code": "UNKNOWN_ERROR"
            }
    
    async def _verify_otp_async(self, session_key: str, otp_code: str, 
                                phone_number: str, phone_code_hash: str) -> Dict[str, Any]:
        """Verify OTP - Fresh client for each attempt"""
        try:
            # Get session info
            with self.sessions_lock:
                if session_key not in self.login_sessions:
                    return {
                        "success": False,
                        "error": "Session expired",
                        "error_code": "SESSION_EXPIRED"
                    }
                
                session_data = self.login_sessions[session_key]
                device = session_data["device"]
            
            # Check expiration
            if time.time() > session_data["expires_at"]:
                with self.sessions_lock:
                    self.login_sessions.pop(session_key, None)
                
                return {
                    "success": False,
                    "error": "Code expired. Request new OTP.",
                    "error_code": "CODE_EXPIRED"
                }
            
            # Create FRESH client
            client = None
            try:
                timestamp = int(time.time())
                client = Client(
                    name=f"verify_{phone_number}_{timestamp}",
                    api_id=self.api_id,
                    api_hash=self.api_hash,
                    device_model=device["device_model"],
                    system_version=device["system_version"],
                    app_version=device["app_version"],
                    lang_code=device["lang_code"],
                    in_memory=True,
                    no_updates=True
                )
                
                await client.connect()
                
                # Try to sign in
                try:
                    await client.sign_in(
                        phone_number=phone_number,
                        phone_code=otp_code,
                        phone_code_hash=phone_code_hash
                    )
                    has_2fa = False
                    
                except SessionPasswordNeeded:
                    # Update session with client info for 2FA
                    with self.sessions_lock:
                        self.login_sessions[session_key]["client_info"] = {
                            "device": device,
                            "phone": phone_number
                        }
                    
                    await client.disconnect()
                    
                    return {
                        "success": False,
                        "needs_2fa": True,
                        "session_key": session_key
                    }
                    
                except PhoneCodeInvalid:
                    await client.disconnect()
                    return {
                        "success": False,
                        "error": "Invalid code",
                        "error_code": "INVALID_CODE"
                    }
                    
                except PhoneCodeExpired:
                    with self.sessions_lock:
                        self.login_sessions.pop(session_key, None)
                    
                    await client.disconnect()
                    return {
                        "success": False,
                        "error": "Code expired. Request new OTP.",
                        "error_code": "CODE_EXPIRED"
                    }
                
                # Success - get session
                session_string = await client.export_session_string()
                encrypted_session = self.encryption.encrypt(session_string)
                
                # Get account info
                try:
                    me = await client.get_me()
                    account_info = {
                        "user_id": me.id,
                        "first_name": me.first_name,
                        "phone_number": me.phone_number
                    }
                except:
                    account_info = {}
                
                await client.disconnect()
                
                # Cleanup session
                with self.sessions_lock:
                    self.login_sessions.pop(session_key, None)
                
                # Update stats
                self.stats["logins_successful"] += 1
                
                logger.info(f"Login successful: {phone_number}")
                
                return {
                    "success": True,
                    "session_string": encrypted_session,
                    "raw_session": session_string,
                    "has_2fa": has_2fa,
                    "device_info": device,
                    "account_info": account_info
                }
                
            except Exception as e:
                if client:
                    await client.disconnect()
                raise
                
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            self.stats["logins_failed"] += 1
            
            # Cleanup on error
            with self.sessions_lock:
                self.login_sessions.pop(session_key, None)
            
            return {
                "success": False,
                "error": str(e),
                "error_code": "VERIFICATION_FAILED"
            }
    
    async def _verify_2fa_async(self, session_key: str, password: str) -> Dict[str, Any]:
        """Verify 2FA"""
        try:
            with self.sessions_lock:
                if session_key not in self.login_sessions:
                    return {
                        "success": False,
                        "error": "Session expired",
                        "error_code": "SESSION_EXPIRED"
                    }
                
                session_data = self.login_sessions[session_key]
                device = session_data.get("device", self.device_manager.get_random_device())
                phone = session_data.get("phone", "")
            
            # Create fresh client
            timestamp = int(time.time())
            client = Client(
                name=f"verify2fa_{phone}_{timestamp}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
                lang_code=device["lang_code"],
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            # Check password
            try:
                await client.check_password(password)
            except:
                await client.disconnect()
                return {
                    "success": False,
                    "error": "Invalid password",
                    "error_code": "INVALID_PASSWORD"
                }
            
            # Get session
            session_string = await client.export_session_string()
            encrypted_session = self.encryption.encrypt(session_string)
            
            # Get account info
            try:
                me = await client.get_me()
                account_info = {
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "phone_number": me.phone_number
                }
            except:
                account_info = {}
            
            await client.disconnect()
            
            # Cleanup
            with self.sessions_lock:
                self.login_sessions.pop(session_key, None)
            
            # Update stats
            self.stats["logins_successful"] += 1
            
            logger.info(f"2FA login successful: {phone}")
            
            return {
                "success": True,
                "session_string": encrypted_session,
                "raw_session": session_string,
                "has_2fa": True,
                "two_step_password": password,
                "device_info": device,
                "account_info": account_info
            }
            
        except Exception as e:
            logger.error(f"2FA error: {e}")
            
            with self.sessions_lock:
                self.login_sessions.pop(session_key, None)
            
            return {
                "success": False,
                "error": "Invalid password",
                "error_code": "INVALID_PASSWORD"
            }
    
    async def _get_latest_otp_async(self, session_string: str, phone: str) -> Optional[str]:
        """Get latest OTP"""
        client = None
        try:
            # Decrypt session
            try:
                decrypted_session = self.encryption.decrypt(session_string)
            except:
                decrypted_session = session_string
            
            # Create client
            device = self.device_manager.get_random_device()
            client = Client(
                name=f"otp_fetch_{int(time.time())}",
                session_string=decrypted_session,
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_model=device["device_model"],
                system_version=device["system_version"],
                app_version=device["app_version"],
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            latest_otp = None
            
            # Check Telegram (777000)
            try:
                async for message in client.get_chat_history(777000, limit=20):
                    if message.text:
                        otp_matches = re.findall(r'\b\d{5,6}\b', message.text)
                        if otp_matches:
                            latest_otp = otp_matches[0]
                            break
            except:
                pass
            
            # Check "Telegram" chat
            if not latest_otp:
                try:
                    async for message in client.get_chat_history("Telegram", limit=20):
                        if message.text and ("code" in message.text.lower()):
                            otp_matches = re.findall(r'\b\d{5,6}\b', message.text)
                            if otp_matches:
                                latest_otp = otp_matches[0]
                                break
                except:
                    pass
            
            await client.disconnect()
            
            if latest_otp:
                logger.info(f"OTP found for {phone}")
            
            return latest_otp
            
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            if client:
                await client.disconnect()
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics"""
        stats = self.stats.copy()
        with self.sessions_lock:
            stats.update({
                "active_sessions": len(self.login_sessions),
                "uptime": int(time.time() - self.stats["start_time"])
            })
        return stats
    
    def cleanup(self):
        """Cleanup expired sessions"""
        current_time = time.time()
        expired_keys = []
        
        with self.sessions_lock:
            for key, session in self.login_sessions.items():
                if current_time > session.get("expires_at", 0):
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self.login_sessions[key]
        
        if expired_keys:
            logger.debug(f"Cleaned {len(expired_keys)} expired sessions")


# ========================
# FACTORY FUNCTION
# ========================
def create_account_manager(api_id: int, api_hash: str, 
                          encryption_key: Optional[str] = None) -> ProfessionalAccountManager:
    """Create Account Manager"""
    return ProfessionalAccountManager(api_id, api_hash, encryption_key)
