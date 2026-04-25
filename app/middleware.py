from __future__ import annotations
import ipaddress
import json
from typing import List
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import time

class SecurityMiddleware(BaseHTTPMiddleware):
    """Security middleware for IP whitelisting and request size limiting"""
    
    def __init__(self, app, allowed_ips: List[str] = None, max_request_size: int = 1024*1024):
        super().__init__(app)
        self.allowed_ips = allowed_ips or []
        self.max_request_size = max_request_size
        
    def _is_ip_allowed(self, client_ip: str) -> bool:
        """Check if client IP is in allowed list"""
        if not self.allowed_ips:
            return True  # No restrictions if no IPs specified
            
        try:
            client_addr = ipaddress.ip_address(client_ip)
            for allowed in self.allowed_ips:
                allowed_network = ipaddress.ip_network(allowed, strict=False)
                if client_addr in allowed_network:
                    return True
            return False
        except ipaddress.AddressValueError:
            return False
    
    async def dispatch(self, request: Request, call_next):
        # IP whitelisting
        client_ip = request.client.host if request.client else "unknown"
        
        if not self._is_ip_allowed(client_ip):
            return JSONResponse(
                status_code=403,
                content={"error": "Access denied"}
            )
        
        # Request size limiting
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_request_size:
            return JSONResponse(
                status_code=413,
                content={"error": "Request too large"}
            )
        
        response = await call_next(request)
        return response

class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware for collecting request metrics"""
    
    def __init__(self, app):
        super().__init__(app)
        self.request_count = 0
        self.request_duration_sum = 0.0
        self.error_count = 0
        
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        self.request_count += 1
        
        response = await call_next(request)
        
        duration = time.time() - start_time
        self.request_duration_sum += duration
        
        if response.status_code >= 400:
            self.error_count += 1
            
        # Add metrics headers
        response.headers["X-Request-Duration"] = f"{duration:.3f}"
        
        return response
    
    def get_metrics(self) -> dict:
        """Get current metrics"""
        avg_duration = (
            self.request_duration_sum / self.request_count 
            if self.request_count > 0 else 0
        )
        
        return {
            "requests_total": self.request_count,
            "requests_duration_seconds_sum": self.request_duration_sum,
            "requests_duration_seconds_avg": avg_duration,
            "errors_total": self.error_count,
            "error_rate": self.error_count / self.request_count if self.request_count > 0 else 0
        }