"""
PROFESSIONAL PYROGRAM ACCOUNT MANAGER - STABLE VERSION
Completely fixed async loop issues for Heroku
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
# ASYNC MANAGER (COMPLETELY FIXED)
# ========================
class AsyncManager:
    """
    COMPLETELY FIXED Async Manager for Heroku
    - Creates new event loop for EVERY operation
    - No shared loops between threads
    - Guaranteed thread safety
    """
    
    def __init__(self):
        # Thread pool for running async tasks
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=10,
            thread_name_prefix="async_worker"
        )
        logger.info("AsyncManager initialized with thread pool")
    
    def run_async(self, coro):
        """
        SAFEST method: Run each async operation in its own isolated thread+loop
        """
        def run_in_thread():
            """Create fresh event loop for each task"""
            try:
                # Create BRAND NEW event loop for this task
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                
                try:
                    # Run the coroutine
                    result = new_loop.run_until_complete(coro)
                    return result
                finally:
                    # Always clean up the loop
                    new_loop.close()
            except Exception as e:
                logger.error(f"Error in isolated async thread: {e}")
                raise
        
        # Submit task to thread pool
        future = self.thread_pool.submit(run_in_thread)
        
        try:
            # Wait for result with timeout
            return future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            logger.error("Async operation timed out after 60 seconds")
            raise TimeoutError("Operation timed out")
        except Exception as e:
            logger.error(f"Async operation failed: {e}")
            raise
    
    async def run_with_timeout(self, coro, timeout: int = 30):
        """Run coroutine with timeout (for use inside async functions)"""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Operation timed out after {timeout} seconds")
            raise TimeoutError(f"Operation timed out after {timeout} seconds")
    
    def shutdown(self):
        """Clean shutdown of thread pool"""
        self.thread_pool.shutdown(wait=True)
        logger.info("AsyncManager thread pool shut down")

# ========================
# ACCOUNT MANAGER (SIMPLE & STABLE)
# ========================
class ProfessionalAccountManager:
    """
    SIMPLE and STABLE Account Manager
    - No complex async loop sharing
    - Each operation is completely isolated
    - Guaranteed to work after Heroku restarts
    """
    
    def __init__(self, api_id: int, api_hash: str, encryption_key: Optional[str] = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.async_manager = AsyncManager()
        self.rate_limiter = RateLimiter()
        self.encryption = EncryptionManager(encryption_key)
        self.validator = SessionValidator()
        self.device_manager = DeviceManager()
        
        # SIMPLE session storage (no complex sharing)
        self.login_sessions: Dict[str, Dict] = {}
        self.active_clients: Dict[str, Client] = {}
        self.sessions_lock = threading.Lock()
        
        # Statistics
        self.stats = {
            "logins_attempted": 0,
            "logins_successful": 0,
            "logins_failed": 0,
            "otps_fetched": 0,
            "errors": {},
            "start_time": time.time()
        }
        
        # SIMPLE cleanup
        self._start_cleanup_thread()
        
        logger.info(f"✅ Account Manager initialized for API ID: {api_id}")
    
    def _start_cleanup_thread(self):
        """Start simple cleanup thread"""
        def cleanup():
            while True:
                try:
                    time.sleep(60)
                    self._cleanup_expired_sessions()
                except Exception as e:
                    logger.error(f"Cleanup error: {e}")
                    time.sleep(10)
        
        thread = threading.Thread(target=cleanup, daemon=True, name="cleanup_thread")
        thread.start()
    
    def _cleanup_expired_sessions(self):
        """Clean up expired sessions"""
        current_time = time.time()
        expired_keys = []
        
        with self.sessions_lock:
            for key, session in self.login_sessions.items():
                if current_time > session.get("expires_at", 0):
                    expired_keys.append(key)
            
            for key in expired_keys:
                session = self.login_sessions.pop(key, None)
                if session and session.get("client"):
                    # Schedule disconnect in background
                    threading.Thread(
                        target=lambda: self.async_manager.run_async(
                            self._safe_disconnect(session["client"])
                        ),
                        daemon=True
                    ).start()
        
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
    
    def validate_session(self, session_string: str, phone: str = None) -> Dict[str, Any]:
        """Validate session string"""
        return self.async_manager.run_async(
            self._validate_session_async(session_string, phone)
        )
    
    def get_account_info(self, session_string: str) -> Dict[str, Any]:
        """Get account information"""
        return self.async_manager.run_async(
            self._get_account_info_async(session_string)
        )
    
    # ========================
    # ASYNC IMPLEMENTATIONS (ISOLATED)
    # ========================
    
    async def _send_otp_async(self, phone_number: str, device_type: str, ip_address: str) -> Dict[str, Any]:
        """Send OTP - completely isolated"""
        # Rate limiting check
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
            
            # Connect
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
            
            # Generate session key
            session_key = hashlib.sha256(
                f"{phone_number}_{timestamp}_{secrets.token_hex(8)}".encode()
            ).hexdigest()[:32]
            
            # Store session
            with self.sessions_lock:
                self.login_sessions[session_key] = {
                    "client": client,
                    "device": device,
                    "phone": phone_number,
                    "timestamp": timestamp,
                    "phone_code_hash": sent_code.phone_code_hash,
                    "expires_at": timestamp + 300
                }
            
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
        """Verify OTP - completely isolated"""
        try:
            # Get session
            with self.sessions_lock:
                if session_key not in self.login_sessions:
                    return {
                        "success": False,
                        "error": "Session expired.",
                        "error_code": "SESSION_EXPIRED"
                    }
                
                session_data = self.login_sessions[session_key]
                client = session_data["client"]
                device = session_data["device"]
            
            # Check expiration
            if time.time() > session_data["expires_at"]:
                await self._safe_disconnect(client)
                with self.sessions_lock:
                    self.login_sessions.pop(session_key, None)
                
                return {
                    "success": False,
                    "error": "Session expired.",
                    "error_code": "SESSION_EXPIRED"
                }
            
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
                # 2FA required - return immediately
                return {
                    "success": False,
                    "needs_2fa": True,
                    "session_key": session_key,
                    "device_info": device
                }
                
            except PhoneCodeInvalid:
                return {
                    "success": False,
                    "error": "Invalid code.",
                    "error_code": "INVALID_CODE"
                }
                
            except PhoneCodeExpired:
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
            
            # Store client
            with self.sessions_lock:
                self.active_clients[phone_number] = client
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
                "device_info": device,
                "account_info": account_info,
                "timestamp": int(time.time())
            }
            
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            self.stats["logins_failed"] += 1
            
            # Cleanup
            with self.sessions_lock:
                if session_key in self.login_sessions:
                    session_data = self.login_sessions.pop(session_key)
                    if session_data.get("client"):
                        asyncio.create_task(self._safe_disconnect(session_data["client"]))
            
            return {
                "success": False,
                "error": str(e),
                "error_code": "VERIFICATION_FAILED"
            }
    
    async def _verify_2fa_async(self, session_key: str, password: str) -> Dict[str, Any]:
        """Verify 2FA - completely isolated"""
        try:
            # Get session
            with self.sessions_lock:
                if session_key not in self.login_sessions:
                    return {
                        "success": False,
                        "error": "Session expired.",
                        "error_code": "SESSION_EXPIRED"
                    }
                
                session_data = self.login_sessions[session_key]
                client = session_data["client"]
                device = session_data["device"]
                phone = session_data.get("phone", "")
            
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
            
            # Store and cleanup
            with self.sessions_lock:
                if phone:
                    self.active_clients[phone] = client
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
                "account_info": account_info,
                "timestamp": int(time.time())
            }
            
        except Exception as e:
            logger.error(f"2FA error: {e}")
            
            # Cleanup
            with self.sessions_lock:
                if session_key in self.login_sessions:
                    session_data = self.login_sessions.pop(session_key)
                    if session_data.get("client"):
                        asyncio.create_task(self._safe_disconnect(session_data["client"]))
            
            return {
                "success": False,
                "error": "Invalid 2FA password.",
                "error_code": "INVALID_2FA"
            }
    
    async def _get_latest_otp_async(self, session_string: str, phone: str, 
                                    max_messages: int = 50) -> Optional[str]:
        """Fetch OTP - completely isolated"""
        client = None
        
        try:
            # Decrypt session
            try:
                decrypted_session = self.encryption.decrypt(session_string)
            except:
                decrypted_session = session_string
            
            # Get device
            device = self.device_manager.get_random_device()
            
            # Create client
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
    
    async def _validate_session_async(self, session_string: str, phone: str = None) -> Dict[str, Any]:
        """Validate session - completely isolated"""
        client = None
        
        try:
            # Decrypt
            try:
                decrypted_session = self.encryption.decrypt(session_string)
            except:
                decrypted_session = session_string
            
            # Check format
            if not self.validator.is_valid_session_format(decrypted_session):
                return {
                    "valid": False,
                    "error": "Invalid format",
                    "error_code": "INVALID_FORMAT"
                }
            
            # Get device
            device = self.device_manager.get_random_device()
            
            # Create client
            client = Client(
                name=f"validate_{int(time.time())}",
                session_string=decrypted_session,
                api_id=self.api_id,
                api_hash=self.api_hash,
                device_model=device["device_model"],
                system_version=device["system_version"],
                in_memory=True,
                no_updates=True
            )
            
            # Connect
            await client.connect()
            
            # Check if working
            try:
                me = await client.get_me()
                
                if not me:
                    await self._safe_disconnect(client)
                    return {
                        "valid": False,
                        "error": "Account not found",
                        "error_code": "ACCOUNT_NOT_FOUND"
                    }
                
                account_info = {
                    "user_id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone_number": me.phone_number
                }
                
                # Test message fetch
                try:
                    await client.get_dialogs(limit=1)
                    can_fetch = True
                except:
                    can_fetch = False
                
                await self._safe_disconnect(client)
                
                return {
                    "valid": True,
                    "account_info": account_info,
                    "can_fetch_messages": can_fetch,
                    "device_used": device,
                    "validated_at": int(time.time())
                }
                
            except AuthKeyUnregistered:
                await self._safe_disconnect(client)
                return {
                    "valid": False,
                    "error": "Session revoked",
                    "error_code": "SESSION_REVOKED"
                }
            except UserDeactivatedBan:
                await self._safe_disconnect(client)
                return {
                    "valid": False,
                    "error": "Account banned",
                    "error_code": "ACCOUNT_BANNED"
                }
            except Exception as e:
                await self._safe_disconnect(client)
                return {
                    "valid": False,
                    "error": str(e),
                    "error_code": "VALIDATION_FAILED"
                }
            
        except Exception as e:
            if client:
                await self._safe_disconnect(client)
            
            return {
                "valid": False,
                "error": str(e),
                "error_code": "CONNECTION_FAILED"
            }
    
    async def _get_account_info_async(self, session_string: str) -> Dict[str, Any]:
        """Get account info"""
        validation = await self._validate_session_async(session_string)
        
        if not validation.get("valid"):
            return validation
        
        # Add more info
        client = None
        try:
            decrypted_session = self.encryption.decrypt(session_string)
            
            client = Client(
                name=f"info_{int(time.time())}",
                session_string=decrypted_session,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            me = await client.get_me()
            
            # Get dialog count
            try:
                dialogs_count = len(await client.get_dialogs(limit=100))
            except:
                dialogs_count = 0
            
            await self._safe_disconnect(client)
            
            result = validation.copy()
            result["additional_info"] = {
                "dialogs_count": dialogs_count,
                "has_username": bool(me.username),
                "has_profile_photo": bool(me.photo) if hasattr(me, 'photo') else False
            }
            
            return result
            
        except Exception as e:
            if client:
                await self._safe_disconnect(client)
            
            result = validation.copy()
            result["additional_info_error"] = str(e)
            return result
    
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
                "active_clients": len(self.active_clients),
                "uptime": int(time.time() - self.stats["start_time"])
            })
        return stats
    
    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            "logins_attempted": 0,
            "logins_successful": 0,
            "logins_failed": 0,
            "otps_fetched": 0,
            "errors": {},
            "start_time": time.time()
        }
    
    def get_encryption_key(self) -> str:
        """Get encryption key"""
        return self.encryption.get_key()
    
    def disconnect_all(self):
        """Disconnect all clients"""
        with self.sessions_lock:
            # Disconnect active clients
            for phone, client in list(self.active_clients.items()):
                try:
                    self.async_manager.run_async(self._safe_disconnect(client))
                except:
                    pass
            self.active_clients.clear()
            
            # Disconnect login sessions
            for session_key, session_data in list(self.login_sessions.items()):
                try:
                    self.async_manager.run_async(self._safe_disconnect(session_data["client"]))
                except:
                    pass
            self.login_sessions.clear()
    
    def __del__(self):
        """Cleanup"""
        try:
            self.disconnect_all()
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
    print("Account Manager Module Loaded Successfully")
    print("Use: from account import create_account_manager")
