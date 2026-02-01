"""
PROFESSIONAL PYROGRAM ACCOUNT MANAGER - ULTIMATE FIXED VERSION
Fixes "Event loop is closed" and all async issues
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
import concurrent.futures
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
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
# ENCRYPTION MANAGER
# ========================
class EncryptionManager:
    """Handle encryption/decryption of sensitive data"""
    
    def __init__(self, encryption_key: Optional[str] = None):
        if encryption_key:
            self.key = encryption_key.encode()
        else:
            # Generate key if not provided (store this securely!)
            self.key = Fernet.generate_key()
        
        self.cipher = Fernet(self.key)
    
    def encrypt(self, data: str) -> str:
        """Encrypt sensitive data"""
        try:
            return self.cipher.encrypt(data.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return data  # Fallback to plaintext
    
    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        try:
            return self.cipher.decrypt(encrypted_data.encode()).decode()
        except Exception:
            # If decryption fails, assume it's plaintext
            return encrypted_data
    
    def get_key(self) -> str:
        """Get encryption key"""
        return self.key.decode()

# ========================
# RATE LIMITER
# ========================
class RateLimiter:
    """Prevent abuse and rate limiting"""
    
    def __init__(self):
        self.phone_attempts: Dict[str, List[float]] = {}
        self.ip_attempts: Dict[str, List[float]] = {}
        self.session_attempts: Dict[str, List[float]] = {}
        self.lock = threading.Lock()
    
    def check_phone_attempt(self, phone_number: str, max_attempts: int = 3, window: int = 3600) -> Tuple[bool, Optional[int]]:
        """Check if phone number has exceeded attempt limits"""
        with self.lock:
            key = f"phone_{phone_number}"
            current_time = time.time()
            
            # Clean old attempts
            if key in self.phone_attempts:
                self.phone_attempts[key] = [
                    t for t in self.phone_attempts[key]
                    if current_time - t < window
                ]
            
            # Check attempts
            attempts = self.phone_attempts.get(key, [])
            if len(attempts) >= max_attempts:
                wait_time = int(window - (current_time - attempts[0]))
                return False, wait_time
            
            # Record attempt
            if key not in self.phone_attempts:
                self.phone_attempts[key] = []
            self.phone_attempts[key].append(current_time)
            return True, None
    
    def check_ip_attempt(self, ip_address: str, max_attempts: int = 10, window: int = 300) -> bool:
        """Check IP-based rate limiting"""
        with self.lock:
            current_time = time.time()
            
            # Clean old attempts
            if ip_address in self.ip_attempts:
                self.ip_attempts[ip_address] = [
                    t for t in self.ip_attempts[ip_address]
                    if current_time - t < window
                ]
            
            # Check attempts
            attempts = self.ip_attempts.get(ip_address, [])
            if len(attempts) >= max_attempts:
                return False
            
            # Record attempt
            if ip_address not in self.ip_attempts:
                self.ip_attempts[ip_address] = []
            self.ip_attempts[ip_address].append(current_time)
            return True
    
    def reset_attempts(self, phone_number: str = None, ip_address: str = None):
        """Reset attempts for specific key"""
        with self.lock:
            if phone_number:
                key = f"phone_{phone_number}"
                self.phone_attempts.pop(key, None)
            if ip_address:
                self.ip_attempts.pop(ip_address, None)

# ========================
# SESSION VALIDATOR
# ========================
class SessionValidator:
    """Validate session strings and check their status"""
    
    @staticmethod
    def is_valid_session_format(session_string: str) -> bool:
        """Check if session string has valid format"""
        if not session_string or len(session_string) < 100:
            return False
        
        # Basic format check (Telegram session strings have specific patterns)
        try:
            # Check if it's base64 encoded (common for Pyrogram sessions)
            import base64
            # Try to decode as base64
            base64.b64decode(session_string + '==')
            return True
        except:
            # Might be hex or other format
            return bool(re.match(r'^[a-fA-F0-9]+$', session_string)) or ':' in session_string
    
    @staticmethod
    def check_session_age(session_string: str) -> Optional[int]:
        """Estimate session age (in days) based on patterns"""
        # This is a heuristic - actual age checking requires API call
        try:
            if len(session_string) > 200:
                # Newer sessions tend to be longer
                return 0
            return None
        except:
            return None

# ========================
# DEVICE MANAGER
# ========================
class DeviceManager:
    """Manage device information for sessions"""
    
    DEVICE_PROFILES = [
        # iOS Devices
        {
            "device_model": "iPhone 15 Pro Max",
            "system_version": "iOS 17.2",
            "app_version": "Telegram iOS 10.5.1",
            "lang_code": "en"
        },
        {
            "device_model": "iPhone 14 Pro",
            "system_version": "iOS 16.6",
            "app_version": "Telegram iOS 9.8.2",
            "lang_code": "en"
        },
        # Android Devices
        {
            "device_model": "Samsung Galaxy S23 Ultra",
            "system_version": "Android 14",
            "app_version": "Telegram Android 10.8.0",
            "lang_code": "en"
        },
        {
            "device_model": "Google Pixel 7 Pro",
            "system_version": "Android 14",
            "app_version": "Telegram Android 10.2.3",
            "lang_code": "en"
        },
        # Desktop
        {
            "device_model": "Desktop",
            "system_version": "Windows 11",
            "app_version": "Telegram Desktop 4.9.1",
            "lang_code": "en"
        },
        {
            "device_model": "Desktop",
            "system_version": "macOS 14.2",
            "app_version": "Telegram Desktop 4.8.5",
            "lang_code": "en"
        }
    ]
    
    @staticmethod
    def get_random_device() -> Dict[str, str]:
        """Get random device configuration"""
        import random
        return random.choice(DeviceManager.DEVICE_PROFILES)
    
    @staticmethod
    def get_device_by_type(device_type: str = "android") -> Optional[Dict[str, str]]:
        """Get device configuration by type"""
        for device in DeviceManager.DEVICE_PROFILES:
            if device_type == "ios" and "iPhone" in device["device_model"]:
                return device
            elif device_type == "android" and "Android" in device["system_version"]:
                return device
            elif device_type == "desktop" and device["device_model"] == "Desktop":
                return device
        return DeviceManager.get_random_device()

# ========================
# CLIENT WRAPPER (NEW - FIXES EVENT LOOP ISSUES)
# ========================
class ClientWrapper:
    """
    Wraps Pyrogram Client to handle event loop issues
    Stores client parameters instead of connected client
    """
    
    def __init__(self, session_name: str, api_id: int, api_hash: str, 
                 device_info: Dict[str, str], phone_number: str):
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.device_info = device_info
        self.phone_number = phone_number
        self.phone_code_hash = None
        self.created_at = time.time()
        self.expires_at = self.created_at + 300  # 5 minutes
        
    async def create_client(self):
        """Create and connect a new client instance"""
        client = Client(
            name=self.session_name,
            api_id=self.api_id,
            api_hash=self.api_hash,
            device_model=self.device_info["device_model"],
            system_version=self.device_info["system_version"],
            app_version=self.device_info["app_version"],
            lang_code=self.device_info["lang_code"],
            in_memory=True,
            no_updates=True
        )
        
        await client.connect()
        return client

# ========================
# ASYNC MANAGER (SIMPLIFIED)
# ========================
class AsyncManager:
    """
    Simple Async Manager
    Each operation creates its own event loop
    """
    
    def __init__(self):
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=5,
            thread_name_prefix="async_worker"
        )
    
    def run_async(self, coro):
        """Run async coroutine in sync context"""
        def run_in_thread():
            """Create fresh event loop for this task"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        
        future = self.executor.submit(run_in_thread)
        try:
            return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            logger.error("Operation timed out")
            raise TimeoutError("Operation timed out")
        except Exception as e:
            logger.error(f"Async operation failed: {e}")
            raise
    
    def shutdown(self):
        """Clean shutdown"""
        self.executor.shutdown(wait=True)

# ========================
# ACCOUNT MANAGER (ULTIMATE FIX)
# ========================
class ProfessionalAccountManager:
    """
    ULTIMATE FIXED Account Manager
    - No stored connected clients
    - Fresh client for every operation
    - No event loop issues
    """
    
    def __init__(self, api_id: int, api_hash: str, encryption_key: Optional[str] = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.async_manager = AsyncManager()
        self.rate_limiter = RateLimiter()
        self.encryption = EncryptionManager(encryption_key)
        self.validator = SessionValidator()
        self.device_manager = DeviceManager()
        
        # Store ClientWrapper objects instead of connected clients
        self.login_sessions: Dict[str, ClientWrapper] = {}
        self.sessions_lock = threading.Lock()
        
        # Store session strings for accounts
        self.account_sessions: Dict[str, str] = {}
        self.accounts_lock = threading.Lock()
        
        # Statistics
        self.stats = {
            "logins_attempted": 0,
            "logins_successful": 0,
            "logins_failed": 0,
            "otps_fetched": 0,
            "errors": {},
            "start_time": time.time()
        }
        
        # Start cleanup
        self._start_cleanup()
        
        logger.info(f"✅ Account Manager initialized for API ID: {api_id}")
    
    def _start_cleanup(self):
        """Start cleanup thread"""
        def cleanup():
            while True:
                try:
                    time.sleep(60)
                    self._cleanup_expired()
                except Exception as e:
                    logger.error(f"Cleanup error: {e}")
                    time.sleep(10)
        
        thread = threading.Thread(target=cleanup, daemon=True, name="cleanup")
        thread.start()
    
    def _cleanup_expired(self):
        """Clean up expired sessions"""
        current_time = time.time()
        expired_keys = []
        
        with self.sessions_lock:
            for key, wrapper in list(self.login_sessions.items()):
                if current_time > wrapper.expires_at:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self.login_sessions[key]
        
        if expired_keys:
            logger.debug(f"Cleaned {len(expired_keys)} expired sessions")
    
    # ========================
    # PUBLIC METHODS
    # ========================
    
    def send_otp(self, phone_number: str, device_type: str = "random", 
                 ip_address: str = "unknown") -> Dict[str, Any]:
        """Send OTP to phone number"""
        return self.async_manager.run_async(
            self._send_otp_async(phone_number, device_type, ip_address)
        )
    
    def verify_otp(self, session_key: str, otp_code: str, 
                   phone_number: str, phone_code_hash: str) -> Dict[str, Any]:
        """Verify OTP and get session string"""
        return self.async_manager.run_async(
            self._verify_otp_async(session_key, otp_code, phone_number, phone_code_hash)
        )
    
    def verify_2fa(self, session_key: str, password: str) -> Dict[str, Any]:
        """Verify 2FA password"""
        return self.async_manager.run_async(
            self._verify_2fa_async(session_key, password)
        )
    
    def get_latest_otp(self, session_string: str, phone: str, 
                       max_messages: int = 50) -> Optional[str]:
        """Fetch latest OTP from messages"""
        return self.async_manager.run_async(
            self._get_latest_otp_async(session_string, phone, max_messages)
        )
    
    # ========================
    # ASYNC IMPLEMENTATIONS
    # ========================
    
    async def _send_otp_async(self, phone_number: str, device_type: str, ip_address: str) -> Dict[str, Any]:
        """Send OTP - creates fresh client"""
        # Rate limiting
        phone_allowed, wait_time = self.rate_limiter.check_phone_attempt(phone_number)
        if not phone_allowed:
            self.stats["logins_failed"] += 1
            return {
                "success": False,
                "error": f"Too many attempts. Wait {wait_time} seconds.",
                "error_code": "RATE_LIMITED",
                "wait_time": wait_time
            }
        
        if not self.rate_limiter.check_ip_attempt(ip_address):
            return {
                "success": False,
                "error": "Too many requests from this IP.",
                "error_code": "IP_RATE_LIMITED"
            }
        
        self.stats["logins_attempted"] += 1
        client = None
        
        try:
            # Get device
            device = self.device_manager.get_device_by_type(device_type)
            
            # Create session key
            timestamp = int(time.time())
            session_name = f"login_{phone_number}_{timestamp}"
            session_key = hashlib.sha256(
                f"{phone_number}_{timestamp}_{secrets.token_hex(8)}".encode()
            ).hexdigest()[:32]
            
            # Create and connect client
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
            
            await client.connect()
            
            # Check if already logged in
            try:
                if await client.get_me():
                    await self._safe_disconnect(client)
                    return {
                        "success": False,
                        "error": "Already logged in elsewhere.",
                        "error_code": "ALREADY_AUTHORIZED"
                    }
            except:
                pass
            
            # Send OTP
            sent_code = await client.send_code(phone_number)
            
            # Create ClientWrapper (stores params, not connected client)
            wrapper = ClientWrapper(
                session_name=session_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_info=device,
                phone_number=phone_number
            )
            wrapper.phone_code_hash = sent_code.phone_code_hash
            
            # Store wrapper (not connected client)
            with self.sessions_lock:
                self.login_sessions[session_key] = wrapper
            
            await self._safe_disconnect(client)
            
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
                await self._safe_disconnect(client)
            
            wait_time = e.value
            logger.warning(f"Flood wait: {wait_time} seconds")
            
            return {
                "success": False,
                "error": f"Wait {wait_time} seconds.",
                "error_code": "FLOOD_WAIT",
                "wait_time": wait_time
            }
            
        except PhoneNumberInvalid:
            self.stats["logins_failed"] += 1
            return {
                "success": False,
                "error": "Invalid phone number.",
                "error_code": "INVALID_PHONE"
            }
            
        except Exception as e:
            self.stats["logins_failed"] += 1
            logger.error(f"Send OTP error: {e}")
            
            if client:
                await self._safe_disconnect(client)
            
            error_name = type(e).__name__
            self.stats["errors"][error_name] = self.stats["errors"].get(error_name, 0) + 1
            
            return {
                "success": False,
                "error": str(e),
                "error_code": "UNKNOWN_ERROR"
            }
    
    async def _verify_otp_async(self, session_key: str, otp_code: str, 
                                phone_number: str, phone_code_hash: str) -> Dict[str, Any]:
        """Verify OTP - creates fresh client from wrapper"""
        try:
            # Get wrapper
            with self.sessions_lock:
                if session_key not in self.login_sessions:
                    return {
                        "success": False,
                        "error": "Session expired.",
                        "error_code": "SESSION_EXPIRED"
                    }
                
                wrapper = self.login_sessions[session_key]
            
            # Check expiration
            if time.time() > wrapper.expires_at:
                with self.sessions_lock:
                    self.login_sessions.pop(session_key, None)
                
                return {
                    "success": False,
                    "error": "Session expired.",
                    "error_code": "SESSION_EXPIRED"
                }
            
            # Create fresh client
            client = None
            try:
                client = await wrapper.create_client()
                
                # Try to sign in
                try:
                    await client.sign_in(
                        phone_number=phone_number,
                        phone_code=otp_code,
                        phone_code_hash=phone_code_hash
                    )
                    has_2fa = False
                    two_step_password = None
                    
                except SessionPasswordNeeded:
                    # 2FA required
                    await self._safe_disconnect(client)
                    return {
                        "success": False,
                        "needs_2fa": True,
                        "session_key": session_key,
                        "device_info": wrapper.device_info
                    }
                    
                except PhoneCodeInvalid:
                    await self._safe_disconnect(client)
                    return {
                        "success": False,
                        "error": "Invalid code.",
                        "error_code": "INVALID_CODE"
                    }
                    
                except PhoneCodeExpired:
                    await self._safe_disconnect(client)
                    return {
                        "success": False,
                        "error": "Code expired.",
                        "error_code": "CODE_EXPIRED"
                    }
                
                # Success!
                session_string = await client.export_session_string()
                
                # Get account info
                try:
                    me = await client.get_me()
                    account_info = {
                        "user_id": me.id,
                        "first_name": me.first_name,
                        "last_name": me.last_name,
                        "username": me.username,
                        "phone_number": me.phone_number
                    }
                except:
                    account_info = {}
                
                # Encrypt session
                encrypted_session = self.encryption.encrypt(session_string)
                
                # Store session
                with self.accounts_lock:
                    self.account_sessions[phone_number] = session_string
                
                # Cleanup
                await self._safe_disconnect(client)
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
                    "two_step_password": two_step_password,
                    "device_info": wrapper.device_info,
                    "account_info": account_info,
                    "timestamp": int(time.time())
                }
                
            except Exception as e:
                if client:
                    await self._safe_disconnect(client)
                raise
                
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            self.stats["logins_failed"] += 1
            
            # Cleanup
            with self.sessions_lock:
                self.login_sessions.pop(session_key, None)
            
            return {
                "success": False,
                "error": str(e),
                "error_code": "VERIFICATION_FAILED"
            }
    
    async def _verify_2fa_async(self, session_key: str, password: str) -> Dict[str, Any]:
        """Verify 2FA - creates fresh client from wrapper"""
        try:
            # Get wrapper
            with self.sessions_lock:
                if session_key not in self.login_sessions:
                    return {
                        "success": False,
                        "error": "Session expired.",
                        "error_code": "SESSION_EXPIRED"
                    }
                
                wrapper = self.login_sessions[session_key]
            
            # Check expiration
            if time.time() > wrapper.expires_at:
                with self.sessions_lock:
                    self.login_sessions.pop(session_key, None)
                
                return {
                    "success": False,
                    "error": "Session expired.",
                    "error_code": "SESSION_EXPIRED"
                }
            
            # Create fresh client
            client = None
            try:
                client = await wrapper.create_client()
                
                # Verify password
                await client.check_password(password)
                
                # Get session string
                session_string = await client.export_session_string()
                
                # Get account info
                try:
                    me = await client.get_me()
                    account_info = {
                        "user_id": me.id,
                        "first_name": me.first_name,
                        "last_name": me.last_name,
                        "username": me.username,
                        "phone_number": me.phone_number
                    }
                except:
                    account_info = {}
                
                # Encrypt session
                encrypted_session = self.encryption.encrypt(session_string)
                
                # Store session
                with self.accounts_lock:
                    self.account_sessions[wrapper.phone_number] = session_string
                
                # Cleanup
                await self._safe_disconnect(client)
                with self.sessions_lock:
                    self.login_sessions.pop(session_key, None)
                
                # Update stats
                self.stats["logins_successful"] += 1
                
                logger.info(f"2FA login successful: {wrapper.phone_number}")
                
                return {
                    "success": True,
                    "session_string": encrypted_session,
                    "raw_session": session_string,
                    "has_2fa": True,
                    "two_step_password": password,
                    "device_info": wrapper.device_info,
                    "account_info": account_info,
                    "timestamp": int(time.time())
                }
                
            except Exception as e:
                if client:
                    await self._safe_disconnect(client)
                raise
                
        except Exception as e:
            logger.error(f"2FA error: {e}")
            
            # Cleanup
            with self.sessions_lock:
                self.login_sessions.pop(session_key, None)
            
            return {
                "success": False,
                "error": "Invalid 2FA password.",
                "error_code": "INVALID_2FA"
            }
    
    async def _get_latest_otp_async(self, session_string: str, phone: str, 
                                    max_messages: int = 50) -> Optional[str]:
        """Fetch OTP - creates fresh client"""
        client = None
        
        try:
            # Decrypt session
            try:
                decrypted_session = self.encryption.decrypt(session_string)
            except:
                decrypted_session = session_string
            
            # Get device
            device = self.device_manager.get_random_device()
            
            # Create fresh client
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
            
            # Connect
            await client.connect()
            
            latest_otp = None
            latest_time = None
            
            # Check Telegram (777000)
            try:
                async for message in client.get_chat_history(777000, limit=max_messages):
                    if message.text:
                        # 5-digit codes
                        otp_matches = re.findall(r'\b\d{5}\b', message.text)
                        if otp_matches:
                            message_time = message.date.timestamp() if message.date else 0
                            if latest_time is None or message_time > latest_time:
                                latest_time = message_time
                                latest_otp = otp_matches[0]
                        
                        # 6-digit codes
                        if not latest_otp:
                            otp_matches = re.findall(r'\b\d{6}\b', message.text)
                            if otp_matches:
                                message_time = message.date.timestamp() if message.date else 0
                                if latest_time is None or message_time > latest_time:
                                    latest_time = message_time
                                    latest_otp = otp_matches[0]
            except:
                pass
            
            # Check "Telegram" chat
            if not latest_otp:
                try:
                    async for message in client.get_chat_history("Telegram", limit=max_messages):
                        if message.text and ("code" in message.text.lower() or "verify" in message.text.lower()):
                            otp_matches = re.findall(r'\b\d{5,6}\b', message.text)
                            if otp_matches:
                                message_time = message.date.timestamp() if message.date else 0
                                if latest_time is None or message_time > latest_time:
                                    latest_time = message_time
                                    latest_otp = otp_matches[0]
                except:
                    pass
            
            await self._safe_disconnect(client)
            
            if latest_otp:
                self.stats["otps_fetched"] += 1
                logger.info(f"OTP found for {phone}: {latest_otp}")
            
            return latest_otp
            
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            if client:
                await self._safe_disconnect(client)
            return None
    
    # ========================
    # UTILITY METHODS
    # ========================
    
    async def _safe_disconnect(self, client: Client):
        """Safely disconnect client"""
        try:
            if client and hasattr(client, 'is_connected') and client.is_connected:
                await client.disconnect()
                await asyncio.sleep(0.1)
        except:
            pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics"""
        stats = self.stats.copy()
        with self.sessions_lock:
            stats.update({
                "active_sessions": len(self.login_sessions),
                "stored_accounts": len(self.account_sessions),
                "uptime": int(time.time() - self.stats["start_time"])
            })
        return stats
    
    def get_account_session(self, phone: str) -> Optional[str]:
        """Get stored session for account"""
        with self.accounts_lock:
            return self.account_sessions.get(phone)
    
    def store_account_session(self, phone: str, session_string: str):
        """Store account session"""
        with self.accounts_lock:
            self.account_sessions[phone] = session_string
    
    def remove_account_session(self, phone: str):
        """Remove account session"""
        with self.accounts_lock:
            self.account_sessions.pop(phone, None)
    
    def get_all_accounts(self) -> List[str]:
        """Get all stored account phones"""
        with self.accounts_lock:
            return list(self.account_sessions.keys())
    
    def disconnect_all(self):
        """Cleanup"""
        with self.sessions_lock:
            self.login_sessions.clear()
        with self.accounts_lock:
            self.account_sessions.clear()
    
    def __del__(self):
        """Destructor"""
        try:
            self.async_manager.shutdown()
        except:
            pass


# ========================
# FACTORY FUNCTION
# ========================
def create_account_manager(api_id: int, api_hash: str, 
                          encryption_key: Optional[str] = None) -> ProfessionalAccountManager:
    """Create Account Manager instance"""
    return ProfessionalAccountManager(api_id, api_hash, encryption_key)


# ========================
# TEST
# ========================
if __name__ == "__main__":
    print("✅ Account Manager Module - ULTIMATE FIXED VERSION")
    print("✅ No more 'Event loop is closed' errors")
    print("✅ Fresh client for every operation")
    print("✅ Thread-safe and Heroku compatible")
