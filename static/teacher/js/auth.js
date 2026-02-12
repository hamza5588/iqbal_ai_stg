/**
 * Authentication System for Iqbal AI
 * Handles user login, registration, session management, and role-based access control
 */

// ============================================================================
// AUTH SYSTEM CONFIGURATION
// ============================================================================

const AUTH_CONFIG = {
  sessionTimeout: 24 * 60 * 60 * 1000, // 24 hours in milliseconds
  tokenKey: 'iqbal_auth_token',
  userKey: 'iqbal_user',
  roleKey: 'iqbal_user_role',
  emailKey: 'iqbal_user_email',
  nameKey: 'iqbal_user_name',
  sessionTimestampKey: 'iqbal_session_timestamp',
  loginRedirectKey: 'iqbal_redirect_after_login',
  // If set, this common demo password will be accepted for any demo user
  demoPassword: 'password123',
  demoAllowCommonPassword: true,
  demoUsers: {
    // STUDENT ACCOUNTS
    'student@iqbalai.com': {
      password: 'password123',
      role: 'student',
      name: 'Student User',
      email: 'student@iqbalai.com',
      id: 'user-s1'
    },
    'sarah@iqbal.edu': {
      password: 'sarah123',
      role: 'student',
      name: 'Sarah Johnson',
      email: 'sarah@iqbal.edu',
      id: 'user-s2'
    },
    'michael@iqbal.edu': {
      password: 'michael123',
      role: 'student',
      name: 'Michael Chen',
      email: 'michael@iqbal.edu',
      id: 'user-s3'
    },
    'john.student@iqbal.edu': {
      password: 'john123',
      role: 'student',
      name: 'John Doe',
      email: 'john.student@iqbal.edu',
      id: 'user-s4'
    },
    'emma@iqbal.edu': {
      password: 'emma123',
      role: 'student',
      name: 'Emma Wilson',
      email: 'emma@iqbal.edu',
      id: 'user-s5'
    },
    
    // TEACHER ACCOUNTS
    'teacher@iqbalai.com': {
      password: 'password123',
      role: 'teacher',
      name: 'Teacher User',
      email: 'teacher@iqbalai.com',
      id: 'user-t1'
    },
    'john@iqbal.edu': {
      password: 'john123',
      role: 'teacher',
      name: 'John Smith',
      email: 'john@iqbal.edu',
      id: 'user-t2'
    },
    'emily@iqbal.edu': {
      password: 'emily123',
      role: 'teacher',
      name: 'Emily Davis',
      email: 'emily@iqbal.edu',
      id: 'user-t3'
    },
    'david@iqbal.edu': {
      password: 'david123',
      role: 'teacher',
      name: 'David Martinez',
      email: 'david@iqbal.edu',
      id: 'user-t4'
    },
    'sophia@iqbal.edu': {
      password: 'sophia123',
      role: 'teacher',
      name: 'Sophia Anderson',
      email: 'sophia@iqbal.edu',
      id: 'user-t5'
    },
    
    // ADMIN ACCOUNTS
    'admin@iqbalai.com': {
      password: 'password123',
      role: 'admin',
      name: 'Admin User',
      email: 'admin@iqbalai.com',
      id: 'user-a1'
    },
    'admin@iqbal.edu': {
      password: 'admin123',
      role: 'admin',
      name: 'System Admin',
      email: 'admin@iqbal.edu',
      id: 'user-a2'
    }
  }
};

// ============================================================================
// AUTH STATE MANAGEMENT
// ============================================================================

class AuthManager {
  constructor() {
    this.currentUser = null;
    this.isAuthenticated = false;
    this.userRole = 'guest';
    this.authToken = null;
    this.initializeAuth();
  }

  /**
   * Initialize authentication on page load
   */
  initializeAuth() {
    // Check for existing session
    const token = localStorage.getItem(AUTH_CONFIG.tokenKey);
    const user = localStorage.getItem(AUTH_CONFIG.userKey);
    const role = localStorage.getItem(AUTH_CONFIG.roleKey);
    const sessionTime = localStorage.getItem(AUTH_CONFIG.sessionTimestampKey);

    if (token && user && sessionTime) {
      // Check if session has expired
      const sessionAge = Date.now() - parseInt(sessionTime);
      if (sessionAge < AUTH_CONFIG.sessionTimeout) {
        // Session is valid
        this.authToken = token;
        this.currentUser = JSON.parse(user);
        this.userRole = role || 'student';
        this.isAuthenticated = true;
        // Update session timestamp
        localStorage.setItem(AUTH_CONFIG.sessionTimestampKey, Date.now().toString());
        console.log('✅ Session restored for user:', this.currentUser.email);
        return true;
      } else {
        // Session expired
        console.log('⏰ Session expired');
        this.logout();
        return false;
      }
    }
    return false;
  }

  /**
   * Login user with email and password
   */
  async login(email, password) {
    try {
      // Validate input
      if (!email || !password) {
        throw new Error('Email and password are required');
      }

      email = email.toLowerCase().trim();

      // Check against demo users (frontend-only)
      if (AUTH_CONFIG.demoUsers[email]) {
        const demoUser = AUTH_CONFIG.demoUsers[email];
        
        // Verify password: accept either the user's stored demo password
        // or the global demo password when demoAllowCommonPassword is enabled
        const acceptsCommon = AUTH_CONFIG.demoAllowCommonPassword &&
          AUTH_CONFIG.demoPassword &&
          password === AUTH_CONFIG.demoPassword;

        if (demoUser.password !== password && !acceptsCommon) {
          throw new Error('Invalid email or password');
        }

        // Create token (simple JWT-like token for demo)
        const token = this.generateToken(email);
        
        // Store session
        this.setSession(token, demoUser);
        
        console.log('✅ Login successful for:', email, 'Role:', demoUser.role);
        return {
          success: true,
          user: demoUser,
          message: 'Login successful'
        };
      } else {
        throw new Error('User not found. Try: student@iqbalai.com / teacher@iqbalai.com / admin@iqbalai.com');
      }
    } catch (error) {
      console.error('❌ Login error:', error.message);
      throw error;
    }
  }

  /**
   * Register a new user (demo mode)
   */
  async register(userData) {
    try {
      const { name, email, password, confirmPassword, role } = userData;

      // Validation
      if (!name || !email || !password || !confirmPassword) {
        throw new Error('All fields are required');
      }

      if (password !== confirmPassword) {
        throw new Error('Passwords do not match');
      }

      if (password.length < 6) {
        throw new Error('Password must be at least 6 characters');
      }

      email = email.toLowerCase().trim();

      // Check if user already exists
      if (AUTH_CONFIG.demoUsers[email]) {
        throw new Error('Email already registered. Try different email or login.');
      }

      // In production, this would call backend API
      // For demo, we'll add to demoUsers
      const newUser = {
        name,
        email,
        password,
        role: role || 'student'
      };

      AUTH_CONFIG.demoUsers[email] = newUser;

      console.log('✅ Registration successful for:', email);
      return {
        success: true,
        message: 'Registration successful. You can now login.',
        user: { name, email, role: newUser.role }
      };
    } catch (error) {
      console.error('❌ Registration error:', error.message);
      throw error;
    }
  }

  /**
   * Set session data in localStorage
   */
  setSession(token, user) {
    this.authToken = token;
    this.currentUser = user;
    this.userRole = user.role;
    this.isAuthenticated = true;

    localStorage.setItem(AUTH_CONFIG.tokenKey, token);
    localStorage.setItem(AUTH_CONFIG.userKey, JSON.stringify(user));
    localStorage.setItem(AUTH_CONFIG.roleKey, user.role);
    localStorage.setItem(AUTH_CONFIG.emailKey, user.email);
    localStorage.setItem(AUTH_CONFIG.nameKey, user.name);
    localStorage.setItem(AUTH_CONFIG.sessionTimestampKey, Date.now().toString());
  }

  /**
   * Logout user
   */
  logout() {
    console.log('👋 Logging out user:', this.currentUser?.email);
    
    this.authToken = null;
    this.currentUser = null;
    this.userRole = 'guest';
    this.isAuthenticated = false;

    // Clear all auth data from localStorage
    localStorage.removeItem(AUTH_CONFIG.tokenKey);
    localStorage.removeItem(AUTH_CONFIG.userKey);
    localStorage.removeItem(AUTH_CONFIG.roleKey);
    localStorage.removeItem(AUTH_CONFIG.emailKey);
    localStorage.removeItem(AUTH_CONFIG.nameKey);
    localStorage.removeItem(AUTH_CONFIG.sessionTimestampKey);

    // Redirect to backend login route (works locally and on staging)
    window.location.href = '/auth/login';
  }

  /**
   * Check if user has specific role
   */
  hasRole(role) {
    if (Array.isArray(role)) {
      return role.includes(this.userRole);
    }
    return this.userRole === role;
  }

  /**
   * Check if user can perform action
   */
  can(action) {
    const permissions = {
      'create-lesson': ['teacher', 'admin'],
      'edit-lesson': ['teacher', 'admin'],
      'delete-lesson': ['teacher', 'admin'],
      'view-faq': ['teacher', 'admin'],
      'view-analytics': ['teacher', 'admin'],
      'manage-users': ['admin'],
      'view-settings': ['student', 'teacher', 'admin']
    };

    const allowedRoles = permissions[action] || [];
    return allowedRoles.includes(this.userRole);
  }

  /**
   * Generate simple token (demo only)
   */
  generateToken(email) {
    const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const payload = btoa(JSON.stringify({
      email,
      timestamp: Date.now(),
      exp: Date.now() + AUTH_CONFIG.sessionTimeout
    }));
    const signature = btoa('demo-signature-key');
    return `${header}.${payload}.${signature}`;
  }

  /**
   * Get current user info
   */
  getCurrentUser() {
    return this.currentUser;
  }

  /**
   * Get user role
   */
  getUserRole() {
    return this.userRole;
  }

  /**
   * Check if authenticated
   */
  isUserAuthenticated() {
    return this.isAuthenticated;
  }

  /**
   * Get authentication token
   */
  getToken() {
    return this.authToken;
  }

  /**
   * Update user profile
   */
  updateUserProfile(updates) {
    if (!this.isAuthenticated) {
      throw new Error('User not authenticated');
    }

    this.currentUser = { ...this.currentUser, ...updates };
    localStorage.setItem(AUTH_CONFIG.userKey, JSON.stringify(this.currentUser));
    
    if (updates.name) {
      localStorage.setItem(AUTH_CONFIG.nameKey, updates.name);
    }

    return this.currentUser;
  }

  /**
   * Change password
   */
  async changePassword(oldPassword, newPassword) {
    if (!this.isAuthenticated) {
      throw new Error('User not authenticated');
    }

    const userEmail = this.currentUser.email;
    const demoUser = AUTH_CONFIG.demoUsers[userEmail];

    if (demoUser.password !== oldPassword) {
      throw new Error('Current password is incorrect');
    }

    demoUser.password = newPassword;
    console.log('✅ Password changed successfully');
    return { success: true, message: 'Password changed successfully' };
  }

  /**
   * Reset password without authentication (demo mode)
   * Used by password recovery flows where the user is not logged in.
   */
  async resetPassword(email, newPassword) {
    try {
      if (!email || !newPassword) {
        throw new Error('Email and new password are required');
      }

      const normalized = email.toLowerCase().trim();
      const demoUser = AUTH_CONFIG.demoUsers[normalized];

      if (!demoUser) {
        throw new Error('No account found for this email');
      }

      // Update demo user password
      demoUser.password = newPassword;

      console.log(`✅ Password reset for ${normalized}`);
      return { success: true, message: 'Password has been reset' };
    } catch (err) {
      console.error('❌ Reset password error:', err.message);
      throw err;
    }
  }
}

// ============================================================================
// GLOBAL AUTH INSTANCE
// ============================================================================

let auth = new AuthManager();
// Expose for pages that reference `window.auth`
try { window.auth = auth; } catch (e) { /* non-browser envs */ }

// ============================================================================
// PROTECTED PAGE GUARD
// ============================================================================

/**
 * Ensure user is authenticated before accessing page
 */
function requireAuth() {
  if (!auth.isUserAuthenticated()) {
    console.log('🔒 Redirecting to login - not authenticated');
    localStorage.setItem(AUTH_CONFIG.loginRedirectKey, window.location.href);
    window.location.href = '/auth/login';
  }
}

/**
 * Ensure user has specific role
 */
function requireRole(allowedRoles) {
  if (!auth.isUserAuthenticated()) {
    localStorage.setItem(AUTH_CONFIG.loginRedirectKey, window.location.href);
    window.location.href = '/auth/login';
    return;
  }

  if (!auth.hasRole(allowedRoles)) {
    alert(`Access denied. This page requires one of these roles: ${allowedRoles.join(', ')}`);
    window.location.href = 'chat.html';
  }
}

/**
 * Ensure user can perform action
 */
function requirePermission(action) {
  if (!auth.isUserAuthenticated()) {
    localStorage.setItem(AUTH_CONFIG.loginRedirectKey, window.location.href);
    window.location.href = '/auth/login';
    return;
  }

  if (!auth.can(action)) {
    alert(`Permission denied. You cannot perform this action.`);
    return false;
  }
  return true;
}

// ============================================================================
// UI HELPER FUNCTIONS
// ============================================================================

/**
 * Update UI to show logged-in user info
 */
function updateAuthUI() {
  if (auth.isUserAuthenticated()) {
    const user = auth.getCurrentUser();
    const role = auth.getUserRole();

    // Update user name/email in UI if elements exist
    const userNameEl = document.getElementById('user-name');
    const userEmailEl = document.getElementById('user-email');
    const userRoleEl = document.getElementById('user-role');
    const userAvatarEl = document.getElementById('user-avatar');

    if (userNameEl) userNameEl.textContent = user.name;
    if (userEmailEl) userEmailEl.textContent = user.email;
    if (userRoleEl) userRoleEl.textContent = role.charAt(0).toUpperCase() + role.slice(1);
    if (userAvatarEl) {
      const initials = user.name.split(' ').map(n => n[0]).join('').toUpperCase();
      userAvatarEl.textContent = initials;
    }

    // Show role-specific elements
    const studentElements = document.querySelectorAll('[data-role="student"]');
    const teacherElements = document.querySelectorAll('[data-role="teacher"]');
    const adminElements = document.querySelectorAll('[data-role="admin"]');

    studentElements.forEach(el => el.style.display = role === 'student' ? 'block' : 'none');
    teacherElements.forEach(el => el.style.display = role === 'teacher' ? 'block' : 'none');
    adminElements.forEach(el => el.style.display = role === 'admin' ? 'block' : 'none');
  }
}

/**
 * Show a consistent loading state on buttons and store original HTML
 */
function setButtonLoading(btn, loadingHtml) {
  if (!btn) return;
  try {
    if (!btn.getAttribute('data-original-html')) {
      btn.setAttribute('data-original-html', btn.innerHTML);
    }
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    btn.innerHTML = loadingHtml || '<span class="loading-dots"><span></span><span></span><span></span></span>';
  } catch (e) {
    console.error('setButtonLoading error', e);
  }
}

/**
 * Restore button to its original HTML and enabled state
 */
function restoreButton(btn) {
  if (!btn) return;
  try {
    const original = btn.getAttribute('data-original-html');
    if (original) {
      btn.innerHTML = original;
    }
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
  } catch (e) {
    console.error('restoreButton error', e);
  }
}

/**
 * Log out user (for use in HTML onclick handlers)
 */
function logout() {
  if (confirm('Are you sure you want to logout?')) {
    auth.logout();
  }
}

/**
 * Get demo credentials display
 */
function getDemoCredentials() {
  return Object.entries(AUTH_CONFIG.demoUsers).map(([email, user]) => ({
    email,
    password: user.password,
    role: user.role,
    name: user.name
  }));
}

// ============================================================================
// DEMO MODE UTILITIES
// ============================================================================

/**
 * Log available demo accounts
 */
function logDemoAccounts() {
  console.log('📝 Demo Accounts Available:');
  console.log('================================');
  Object.entries(AUTH_CONFIG.demoUsers).forEach(([email, user]) => {
    console.log(`Email: ${email}`);
    console.log(`Password: ${user.password}`);
    console.log(`Role: ${user.role}`);
    console.log(`Name: ${user.name}`);
    console.log('---');
  });
}

/**
 * Quick login for testing
 */
function quickLogin(email) {
  const user = AUTH_CONFIG.demoUsers[email];
  if (user) {
    auth.login(email, user.password)
      .then(() => {
        window.location.href = 'chat.html';
      })
      .catch(err => console.error('Quick login failed:', err));
  } else {
    console.error('User not found:', email);
  }
}

// ============================================================================
// API INTEGRATION HELPERS
// ============================================================================

/**
 * Add authentication header to fetch requests
 */
function getAuthHeaders() {
  return {
    'Authorization': `Bearer ${auth.getToken()}`,
    'Content-Type': 'application/json',
    'X-User-Role': auth.getUserRole()
  };
}

/**
 * Authenticated fetch wrapper
 */
async function authFetch(url, options = {}) {
  const headers = {
    ...getAuthHeaders(),
    ...options.headers
  };

  try {
    const response = await fetch(url, { ...options, headers });

    if (response.status === 401) {
      // Unauthorized - session expired
      console.warn('Session expired - logging out');
      auth.logout();
      return;
    }

    if (response.status === 403) {
      // Forbidden - insufficient permissions
      throw new Error('Permission denied. You do not have access to this resource.');
    }

    return response;
  } catch (error) {
    console.error('Fetch error:', error);
    throw error;
  }
}

// ============================================================================
// INITIALIZATION
// ============================================================================

// Initialize auth system when page loads
document.addEventListener('DOMContentLoaded', function() {
  // Log demo accounts to console
  logDemoAccounts();

  // Update UI with current user info
  updateAuthUI();

  // Handle redirect after login
  if (auth.isUserAuthenticated()) {
    const redirectUrl = localStorage.getItem(AUTH_CONFIG.loginRedirectKey);
    if (redirectUrl && window.location.pathname === '/auth/login') {
      localStorage.removeItem(AUTH_CONFIG.loginRedirectKey);
      window.location.href = redirectUrl;
    }
  }

  // Set global userRole for backward compatibility
  if (typeof window !== 'undefined') {
    window.userRole = auth.getUserRole();
    window.currentUser = auth.getCurrentUser();
  }
});

console.log('✅ Authentication System Loaded');
