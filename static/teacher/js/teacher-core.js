/* Extracted verbatim from the former templates/teacher_dashboard.html (removed after the new-UI migration) (main inline script). Do not add Jinja here; use window.TEACHER_CFG. */
      // Chat History Management
      let chatHistory = [];
      let currentChatId = 'chat1';
      let isListening = false;
      let speechRecognition = null;
      let speechSynthesis = null;
      let isTextToSpeechActive = false;
      let mediaRecorder = null;
      let audioChunks = [];
      let mediaStream = null;
      let ttsAudio = null;
      let ttsObjectUrl = null;
      let activeTtsBtn = null;
      let currentChatMenuId = null;
      let lessonCreationCompleted = false; // Flag to track if first lesson is created
      let conversationSummaryRequestToken = 0;
      let lessonSummaryRequestToken = 0;

      // ════════════════════════════════════════════════════════════════════════════════
      // 🔐 CHAT LIFECYCLE SYSTEM - CORE SPECIFICATION
      // ════════════════════════════════════════════════════════════════════════════════
      //
      // PRINCIPLE: Each conversation is FULLY ISOLATED by conversation ID.
      //           Sidebar is NAVIGATION ONLY, not memory.
      //           Context NEVER leaks between chats.
      //
      // ────────────────────────────────────────────────────────────────────────────────
      // 1️⃣  NEW CHAT INITIALIZATION
      // ────────────────────────────────────────────────────────────────────────────────
      // When a user starts a new chat:
      //   ✓ Generate unique conversation ID: chat_<timestamp>
      //   ✓ Initialize empty message array: []
      //   ✓ Mark as active: { active: true }
      //   ✓ Set all other chats to inactive: { active: false }
      //   ✓ Display in sidebar with title: "New Conversation"
      //   ✓ Clear previous context from memory (no carryover)
      //
      // Implementation: startNewChat()
      //
      // ────────────────────────────────────────────────────────────────────────────────
      // 2️⃣  MESSAGE HANDLING & ISOLATION
      // ────────────────────────────────────────────────────────────────────────────────
      // Critical Rules:
      //   ✓ ALL user/AI messages stored ONLY under active conversation ID
      //   ✓ Messages NEVER shared, merged, or inferred across conversation IDs
      //   ✓ appendMessageToChat() uses currentChatId to scope all messages
      //   ✓ loadMessagesForChat(chatId) loads ONLY that chat's messages
      //   ✓ No context bleed: messages are isolated by default
      //
      // Storage Structure:
      //   localStorage['teacher_chat_messages_' + chatId] = [
      //     { role: 'user', content: '...', timestamp: ISO },
      //     { role: 'assistant', content: '...', timestamp: ISO }
      //   ]
      //
      // ────────────────────────────────────────────────────────────────────────────────
      // 3️⃣  CHAT FINALIZATION (Starting New Chat / Limit Reached)
      // ────────────────────────────────────────────────────────────────────────────────
      // When finalization occurs:
      //   ✓ Auto-generate or update sidebar title from first message
      //   ✓ Persist complete conversation: { id, title, messages, updatedAt }
      //   ✓ Create new empty context for next chat (NO carryover)
      //   ✓ Clear any temporary variables/context from previous chat
      //   ✓ Mark previous chat as inactive
      //   ✓ Mark new chat as active
      //
      // Implementation:
      //   - startNewChat() creates new chat and finalizes current
      //   - saveChatThreads() persists metadata
      //   - saveMessagesForChat() persists messages
      //
      // ────────────────────────────────────────────────────────────────────────────────
      // 4️⃣  SIDEBAR PERSISTENCE & NAVIGATION
      // ────────────────────────────────────────────────────────────────────────────────
      // Sidebar contains UI-only state (NAVIGATION, not memory):
      //   ✓ Persisted in localStorage['teacher_chat_threads']
      //   ✓ Contains: array of { id, title, timestamp, active }
      //   ✓ Persists across: page refreshes, browser close/reopen
      //   ✓ NO personalization: just list of past conversation IDs
      //   ✓ Each entry is a \"pointer\" to that conversation's messages
      //
      // Sidebar Display:
      //   ✓ Grouped chronologically: Today, Yesterday, Previous 7 Days, Older
      //   ✓ Only ONE chat marked active at a time
      //   ✓ Click to navigate = loadChat(chatId)
      //
      // ────────────────────────────────────────────────────────────────────────────────
      // 5️⃣  REOPENING A CHAT
      // ────────────────────────────────────────────────────────────────────────────────
      // When user clicks a sidebar chat:
      //   ✓ loadChat(chatId) marks that chat as active
      //   ✓ Fetch ONLY that conversation's messages from localStorage
      //   ✓ Load NO context from other conversations
      //   ✓ Clear previous chat's context from memory
      //   ✓ Restore that conversation as the ONLY active chat
      //   ✓ Display messages in chronological order
      //
      // Implementation: loadChat(chatId)
      //
      // ────────────────────────────────────────────────────────────────────────────────
      // 6️⃣  APPLICATION LOAD BEHAVIOR
      // ────────────────────────────────────────────────────────────────────────────────
      // On page load:
      //   ✓ Load full chat history from localStorage['teacher_chat_threads']
      //   ✓ If chat history exists:
      //     - Auto-select most recent chat (highest timestamp)
      //     - Load that chat's messages via loadChat(mostRecentId)
      //     - Display as active in sidebar and main area
      //   ✓ If no chats exist:
      //     - Show empty state message
      //     - User can create first chat by clicking \"New Chat\" button
      //   ✓ Assistant does NOT assume context from previous sessions
      //
      // Implementation: initializeChatState()
      //
      // ────────────────────────────────────────────────────────────────────────────────
      // 🔒 CORE RULES (Non-Negotiable)
      // ────────────────────────────────────────────────────────────────────────────────
      // ✅ ISOLATION: Each chat is fully isolated by conversation ID
      // ✅ NO BLEED: Messages never leak between conversations
      // ✅ CLEAN START: New chat always starts with empty context
      // ✅ SINGLE SOURCE: Context loaded ONLY from active conversation
      // ✅ ONE ACTIVE: Only one chat marked active at a time
      // ✅ SIDEBAR = UI: Sidebar is navigation (pointer) to conversations, not memory
      // ✅ PERSISTENCE: Metadata and messages persist, but context does not
      //
      // ════════════════════════════════════════════════════════════════════════════════

      // ════════════════════════════════════════════════════════════════════════════════
      // HELPER FUNCTIONS - CONTEXT ISOLATION & CHAT STATE MANAGEMENT
      // ════════════════════════════════════════════════════════════════════════════════

      // Load user info and ensure teacher role (for API integration)
      function loadTeacherUserInfo() {
        return fetch('/user_info', { credentials: 'include' })
          .then(function(res) { return res.json(); })
          .then(function(data) {
            if (!data.success || !data.user) throw new Error('Invalid user info');
            if (data.user.role !== 'teacher' && data.user.role !== 'admin') throw new Error('Teacher access required');
            window.teacherUserInfo = data.user;
            window.userInfo = data.user;
            window.userRole = data.user.role;

            // Ensure any locally persisted teacher data is isolated per account
            try {
              ensureTeacherLocalStorageIsolation(data.user);
            } catch (e) {
              console.error('Failed to enforce teacher local storage isolation', e);
            }

            return data.user;
          });
      }

      // Ensure localStorage keys used for teacher chat/lessons are scoped per teacher account
      function ensureTeacherLocalStorageIsolation(user) {
        if (!user || !user.id) return;
        try {
          const ownerKey = 'teacher_local_owner_id';
          const currentOwner = localStorage.getItem(ownerKey);
          const userIdStr = String(user.id);

          // If owner matches, nothing to do
          if (currentOwner === userIdStr) {
            return;
          }

          // Different teacher logged in on same browser:
          // clear any old, unscoped teacher data so lessons/history don't leak across accounts
          try {
            localStorage.removeItem('teacher_chat_threads');
          } catch (e) {}

          try {
            // Remove any old global chat message keys and lesson caches
            Object.keys(localStorage).forEach(function (key) {
              if (key.startsWith('teacher_chat_messages_')) {
                localStorage.removeItem(key);
              }
              if (key === 'teacher_lessons' || key.startsWith('teacher_lesson_draft_')) {
                localStorage.removeItem(key);
              }
            });
          } catch (e) {
            console.error('Error while clearing old teacher local storage keys', e);
          }

          // Mark new owner
          localStorage.setItem(ownerKey, userIdStr);
        } catch (e) {
          console.error('ensureTeacherLocalStorageIsolation error', e);
        }
      }

      // Clear context when switching chats (prevents leakage)
      function clearChatContext() {
        // Reset the sticky RAG thread/conversation identifiers so a new or
        // switched-to chat can never resume another conversation's LangGraph
        // checkpoint history (was previously a no-op, causing stale answers
        // to bleed across chats).
        window.currentRAGThreadId = null;
        window.currentRAGConversationId = null;
        window.currentLessonChatAlreadySaved = false;
        try {
          localStorage.removeItem('teacher_currentRAGThreadId');
          localStorage.removeItem('teacher_currentRAGConversationId');
        } catch (e) {}
      }

      // Initialize chat state on page load per specification #6
      function initializeChatState() {
        // Load chat history from backend and then render sidebar
        // (uses ConversationModel just like legacy chat.html)
        chatHistory = [];
        loadChatHistoryFromBackend();

        // If chat history exists, load the most recent chat
        // We no longer rely on localStorage-only history here.
        // When backend conversations exist, showChatTab/loadChat will restore them.
        // If none exist yet, we show an empty state until the first chat is created.
      }

      // Show empty state when no chats exist
      function showEmptyChatState() {
        const chatMessages = document.getElementById('chatMessages');
        if (!chatMessages) return;

        chatMessages.innerHTML = `
        <div class="max-w-2xl mx-auto p-8 h-full flex flex-col items-center justify-center text-center">
          <i class="fas fa-comments text-6xl text-gray-300 mb-4"></i>
          <h2 class="text-2xl font-bold text-gray-700 mb-2">No conversations yet</h2>
          <p class="text-gray-500 mb-6">Start your first chat by clicking the button below</p>
          <button onclick="startNewChat()" class="bg-primary-600 text-white px-6 py-2.5 rounded-lg hover:bg-primary-700 transition-colors font-medium">
            <i class="fas fa-plus mr-2"></i>
            Start New Chat
          </button>
        </div>
      `;
      }

      // Show Math Demo Tab with example formulas and ensure only the new content is typeset
      function showMathDemo() {
        stopTextToSpeechSession();
        deactivateAllTabs();
        const mathDemoTabBtn = document.getElementById('mathDemoTabBtn');
        if (mathDemoTabBtn) mathDemoTabBtn.classList.add('active-tab');

        // Hide chat input / prompt area
        const chatInputArea = document.getElementById('chatInputArea');
        const floatingBtn = document.getElementById('floatingActionBtn');
        const promptArea = document.getElementById('promptArea');
        const chatArea = document.getElementById('chatArea');

        if (chatInputArea) chatInputArea.style.display = 'none';
        if (floatingBtn) floatingBtn.classList.add('hidden-fab');
        if (promptArea) promptArea.style.display = 'none';
        if (chatArea) chatArea.style.display = 'block';

        // Demo HTML: inline math, display math, and a code block to show skipping
        const demoHtml = `
        <div class="bg-white rounded-2xl p-6 mx-4 my-4">
          <h2 class="text-xl font-semibold mb-3">MathJax Demo</h2>
          <p>Inline math example: $e^{i\\pi} + 1 = 0$</p>
          <p class="mt-3">Display math example:</p>
          <div class="mt-2">$$\\int_0^1 x^2 \, dx = \\\frac{1}{3}$$</div>
          <h3 class="mt-4">Code block (should be ignored)</h3>
          <pre class="mt-2 bg-gray-50 p-3 rounded"><code>const formula = '$not_math$';</code></pre>
        </div>
      `;

        const chatMessages = document.getElementById('chatMessages');
        // Clear previous and append demo wrapper
        if (chatMessages) {
          chatMessages.innerHTML = '';
          const wrapper = document.createElement('div');
          wrapper.innerHTML = demoHtml;
          chatMessages.appendChild(wrapper);
          // Typeset only the new wrapper
          if (window.typesetMathIn) {
            window.typesetMathIn(wrapper).catch(console.error);
          }
        }
      }

      // Current PDF/Lesson data (stored in localStorage so it persists across tabs)
      let currentPDFData = null;
      let currentLessonMarkdown = null;
      // Namespaced by the logged-in teacher's user id: on a shared/multi-teacher browser
      // (e.g. a lab computer), an un-namespaced key would let one teacher's saved lesson
      // titles block a different teacher from using the same title, even though the
      // backend (LessonModel.check_title_exists) already scopes uniqueness per-teacher.
      const CURRENT_TEACHER_ID = String(window.TEACHER_CFG.teacherId || '');

      function _teacherScopedStorageKey(baseKey) {
        return baseKey + '_' + (CURRENT_TEACHER_ID || 'anon');
      }

      const LESSON_SAVE_SIGNATURES_KEY = 'teacher_saved_lesson_signatures';
      const LESSON_USED_TITLES_KEY = 'teacher_used_lesson_titles';
      const LESSON_SAVE_META_KEY = 'teacher_last_saved_lesson_meta';

      function getSavedLessonSignatures() {
        try {
          const raw = JSON.parse(localStorage.getItem(_teacherScopedStorageKey(LESSON_SAVE_SIGNATURES_KEY)) || '[]');
          return Array.isArray(raw) ? raw : [];
        } catch (e) {
          return [];
        }
      }

      function persistSavedLessonSignatures(signatures) {
        try {
          localStorage.setItem(_teacherScopedStorageKey(LESSON_SAVE_SIGNATURES_KEY), JSON.stringify(Array.isArray(signatures) ? signatures : []));
        } catch (e) {
          console.error('Failed to persist saved lesson signatures', e);
        }
      }

      function getUsedLessonTitles() {
        try {
          const raw = JSON.parse(localStorage.getItem(_teacherScopedStorageKey(LESSON_USED_TITLES_KEY)) || '[]');
          return Array.isArray(raw) ? raw : [];
        } catch (e) {
          return [];
        }
      }

      function persistUsedLessonTitles(titles) {
        try {
          localStorage.setItem(_teacherScopedStorageKey(LESSON_USED_TITLES_KEY), JSON.stringify(Array.isArray(titles) ? titles : []));
        } catch (e) {
          console.error('Failed to persist used lesson titles', e);
        }
      }

      function setSaveLessonButtonsVisible(isVisible) {
        const saveBtn = document.getElementById('saveLessonBtn');
        const helpBtn = document.getElementById('saveLessonHelpBtn');
        const floatingSaveItem = document.getElementById('floatingSaveLessonItem');
        if (saveBtn) {
          if (isVisible) {
            saveBtn.classList.remove('hidden');
            saveBtn.classList.add('md:flex');
          } else {
            saveBtn.classList.add('hidden');
            saveBtn.classList.remove('md:flex');
            saveBtn.classList.remove('save-ready-pulse');
          }
        }
        if (helpBtn) {
          // Keep the help affordance visible whenever the teacher is in a lesson chat.
          const showHelp = isVisible || !!window.currentRAGThreadId || !!(currentPDFData);
          helpBtn.classList.toggle('hidden', !showHelp);
          if (showHelp) helpBtn.classList.add('md:inline-flex');
          else helpBtn.classList.remove('md:inline-flex');
        }
        if (floatingSaveItem) {
          floatingSaveItem.classList.toggle('hidden', !isVisible);
        }
        updateLessonSaveFlowStrip();
      }

      async function getLessonSaveReadiness() {
        const localDraft = !!(currentLessonMarkdown && String(currentLessonMarkdown).trim());
        let lastLessonText = localDraft ? String(currentLessonMarkdown).trim() : '';
        let lessonFinalized = false;
        if (window.currentRAGThreadId) {
          try {
            const res = await fetch(
              '/api/rag/thread/' + encodeURIComponent(window.currentRAGThreadId) + '/finalized-lesson',
              { method: 'GET', credentials: 'include' }
            );
            const data = await res.json().catch(function () { return {}; });
            if (res.ok && data && data.success) {
              if ((data.last_lesson_text || '').trim()) {
                lastLessonText = (data.last_lesson_text || '').trim();
              }
              lessonFinalized = !!data.lesson_finalized;
            }
          } catch (e) {
            console.warn('Failed to evaluate lesson save readiness:', e);
          }
        }
        const hasDraft = !!(lastLessonText && lastLessonText.trim());
        let step = 1;
        if (hasDraft && lessonFinalized) step = 3;
        else if (hasDraft) step = 2;
        return {
          hasDraft: hasDraft,
          lessonFinalized: lessonFinalized,
          lastLessonText: lastLessonText,
          step: step,
          canSaveToMyLessons: hasDraft && lessonFinalized,
        };
      }

      function updateLessonSaveFlowStrip(forcedStep) {
        // Inline strip was removed — it blocked the lesson view above chat.
        // Keep step state for the optional help modal (?) only.
        if (forcedStep) window._lessonSaveFlowStep = forcedStep;
      }

      function ensureLessonSaveGuideModal() {
        let root = document.getElementById('lessonSaveGuideModal');
        if (root) return root;
        root = document.createElement('div');
        root.id = 'lessonSaveGuideModal';
        root.className = 'lesson-save-guide';
        root.setAttribute('role', 'dialog');
        root.setAttribute('aria-modal', 'true');
        root.innerHTML =
          '<div class="lesson-save-guide-card">' +
          '<div class="lesson-save-guide-body">' +
          '<h3 id="lessonSaveGuideTitle">How to save a lesson</h3>' +
          '<p id="lessonSaveGuideLead">Follow these steps so the lesson reaches My Lessons.</p>' +
          '<ol class="lesson-save-guide-list">' +
          '<li data-step="1"><div class="n">1</div><div class="c"><strong>Create the lesson</strong>Upload a PDF / ask IQBAL AI to create the lesson in this chat.</div></li>' +
          '<li data-step="2"><div class="n">2</div><div class="c"><strong>Ask to save in chat</strong>Type “save this lesson” so the draft is finalized. Chat confirmation is not My Lessons yet.</div></li>' +
          '<li data-step="3"><div class="n">3</div><div class="c"><strong>Click Save</strong>Press the Save button to store it in My Lessons. You can repeat this for another lesson in the same chat.</div></li>' +
          '</ol>' +
          '</div>' +
          '<div class="lesson-save-guide-footer">' +
          '<button type="button" class="lesson-save-guide-cancel" id="lessonSaveGuideCancel">Close</button>' +
          '<button type="button" class="lesson-save-guide-ok" id="lessonSaveGuideOk">Got it</button>' +
          '</div></div>';
        document.body.appendChild(root);
        function close() {
          root.classList.remove('is-open');
          document.body.style.overflow = '';
        }
        root.addEventListener('click', function (e) {
          if (e.target === root) close();
        });
        document.getElementById('lessonSaveGuideCancel').addEventListener('click', close);
        document.getElementById('lessonSaveGuideOk').addEventListener('click', function () {
          close();
          const input = document.getElementById('messageInput');
          if (input && window._lessonSaveGuideFocusChat) {
            input.focus();
            if (!input.value.trim() && window._lessonSaveGuidePrefill) {
              input.value = window._lessonSaveGuidePrefill;
              input.dispatchEvent(new Event('input', { bubbles: true }));
            }
          }
        });
        return root;
      }

      function showLessonSaveFlowGuidance(reason) {
        const root = ensureLessonSaveGuideModal();
        const title = document.getElementById('lessonSaveGuideTitle');
        const lead = document.getElementById('lessonSaveGuideLead');
        const step = window._lessonSaveFlowStep || 1;
        let focusStep = step;
        window._lessonSaveGuideFocusChat = false;
        window._lessonSaveGuidePrefill = '';

        if (reason === 'no_content') {
          if (title) title.textContent = 'Create a lesson before saving';
          if (lead) lead.textContent = 'Save is for My Lessons. First create a lesson in this chat, then ask to save it, then click Save.';
          focusStep = 1;
        } else if (reason === 'need_chat_save') {
          if (title) title.textContent = 'Almost — finalize in chat first';
          if (lead) lead.textContent = 'A draft exists, but it is not ready for My Lessons yet. Type “save this lesson” in chat, wait for confirmation, then click Save.';
          focusStep = 2;
          window._lessonSaveGuideFocusChat = true;
          window._lessonSaveGuidePrefill = 'save this lesson';
        } else {
          if (title) title.textContent = 'How to save a lesson';
          if (lead) lead.textContent = 'Use this order every time — including when you create another lesson in the same chat.';
        }

        root.querySelectorAll('.lesson-save-guide-list li').forEach(function (el) {
          const n = parseInt(el.getAttribute('data-step'), 10);
          el.classList.toggle('is-focus', n === focusStep);
        });
        updateLessonSaveFlowStrip(focusStep);
        root.classList.add('is-open');
        document.body.style.overflow = 'hidden';
      }
      window.showLessonSaveFlowGuidance = showLessonSaveFlowGuidance;

      async function refreshSaveLessonAvailability() {
        if (window.currentLessonChatAlreadySaved) {
          setSaveLessonButtonsVisible(false);
          updateLessonSaveFlowStrip(1);
          return;
        }
        const readiness = await getLessonSaveReadiness();
        window._lessonSaveFlowStep = readiness.step;
        // Show Save when a draft exists so premature clicks can open guidance.
        setSaveLessonButtonsVisible(readiness.hasDraft);
        updateLessonSaveFlowStrip(readiness.step);
        const saveBtn = document.getElementById('saveLessonBtn');
        if (saveBtn) {
          if (readiness.canSaveToMyLessons) {
            saveBtn.classList.add('save-ready-pulse');
            saveBtn.title = 'Ready — click to save this lesson to My Lessons';
          } else {
            saveBtn.classList.remove('save-ready-pulse');
            saveBtn.title = readiness.hasDraft
              ? 'Draft ready — first type “save this lesson” in chat, then click Save'
              : 'Save lesson to My Lessons';
          }
        }
      }

      // Save current PDF data to localStorage
      function savePDFDataToStorage() {
        try {
          if (currentPDFData) {
            localStorage.setItem('teacher_current_pdf_data', JSON.stringify(currentPDFData));
          }
          if (currentLessonMarkdown) {
            localStorage.setItem('teacher_current_lesson_markdown', currentLessonMarkdown);
          }
        } catch (e) {
          console.error('Failed to save PDF data', e);
        }
      }

      // Load current PDF data from localStorage
      function loadPDFDataFromStorage() {
        try {
          const pdfData = localStorage.getItem('teacher_current_pdf_data');
          const markdown = localStorage.getItem('teacher_current_lesson_markdown');
          if (pdfData) currentPDFData = JSON.parse(pdfData);
          if (markdown) currentLessonMarkdown = markdown;
        } catch (e) {
          console.error('Failed to load PDF data', e);
        }
      }

      // Clear PDF data from storage
      function clearPDFDataFromStorage() {
        try {
          localStorage.removeItem('teacher_current_pdf_data');
          localStorage.removeItem('teacher_current_lesson_markdown');
          currentPDFData = null;
          currentLessonMarkdown = null;
        } catch (e) {
          console.error('Failed to clear PDF data', e);
        }
      }

      // Initialize on page load
      document.addEventListener('DOMContentLoaded', function () {
        setSaveLessonButtonsVisible(false);
        // Hide loading overlay if it exists
        const loadingOverlay = document.getElementById('loading-overlay');
        if (loadingOverlay) {
          setTimeout(() => {
            loadingOverlay.classList.add('opacity-0', 'pointer-events-none');
          }, 500);
        }

        // Load user info from API and set teacher name; redirect if not teacher
        loadTeacherUserInfo().then(function() {
          const userNameEl = document.getElementById('userName');
          const userAvatarEl = document.getElementById('userAvatar');
          const userNameMobile = document.getElementById('userNameMobile');
          const userAvatarMobile = document.getElementById('userAvatarMobile');
          if (window.teacherUserInfo) {
            const name = window.teacherUserInfo.full_name || window.teacherUserInfo.name || window.teacherUserInfo.username || window.teacherUserInfo.email || 'Teacher';
            const initial = (name || 'T').charAt(0).toUpperCase();
            if (userNameEl) userNameEl.textContent = name;
            if (userAvatarEl) userAvatarEl.textContent = initial;
            if (userNameMobile) userNameMobile.textContent = name;
            if (userAvatarMobile) userAvatarMobile.textContent = initial;
          }
        }).catch(function() {
          window.location.href = '/';
        });

        // Initialize chat state per specification #6
        // If chat history exists, load most recent chat
        // If no chats, show empty state
        initializeChatState();

        // Load any existing PDF/Lesson data from previous session
        loadPDFDataFromStorage();

        // Initialize with My Lessons tab (not Chat tab)
        showMyLessonsPage();

        // Setup textarea auto-resize and validation
        const textarea = document.getElementById('messageInput');
        textarea.addEventListener('input', function () {
          autoResizeTextarea(this);
          updateSendButton();
        });

        // Backend STT/TTS: mic uses MediaRecorder + POST /api/stt; speaker uses POST /api/tts
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          const micBtn = document.getElementById('micBtn');
          if (micBtn) {
            micBtn.disabled = true;
            micBtn.title = 'Microphone not supported';
            micBtn.innerHTML = '<i class="fas fa-microphone-slash"></i>';
          }
        }
        speechSynthesis = window.speechSynthesis;

        // Focus on input
        textarea.focus();

        // Close dropdowns when clicking outside
        document.addEventListener('click', function (event) {
          if (!event.target.closest('.dropdown-menu') && !event.target.closest('[onclick*="toggle"]')) {
            closeAllDropdowns();
          }
        });
      });

      // ================================
      // CHAT PERSISTENCE SYSTEM
      // ================================
      // This system ensures chat history persists via backend conversations
      // and mirrors the legacy chat.html behavior.
      //
      // Source of truth:
      //   - Conversation list: GET /get_conversations
      //   - Messages per conversation: GET /get_messages/<conversation_id>
      //
      // We still use localStorage minimally to remember the last-opened chat
      // and the mapping between sidebar chat IDs and backend conversation IDs.

      function getChatStoragePrefix() {
        const user = window.teacherUserInfo || window.userInfo || {};
        if (user && user.id) {
          return 'teacher_' + String(user.id) + '_';
        }
        return 'teacher_anon_';
      }

      function getLastConversationKey() {
        return getChatStoragePrefix() + 'last_conversation_id';
      }

      // Was referenced (save/load/delete/reset chat) but never defined in the legacy page.
      function getChatMessagesKey(chatId) {
        return getChatStoragePrefix() + 'chat_messages_' + String(chatId);
      }

      // ================================
      // CHAT LIFECYCLE AND BEHAVIOR
      // ================================
      // LIFECYCLE OF A CHAT (Step by Step):
      //
      // 1️⃣  NEW CHAT STARTS
      //     - startNewChat() creates ID: chat_<timestamp>
      //     - Initial title: "New Conversation"
      //     - Set as active (active: true), others inactive
      //
      // 2️⃣  MESSAGES STORED PER CHAT ID
      //     - localStorage['teacher_chat_messages_' + chatId] stores all messages
      //     - Each: { role, content, timestamp }
      //     - addUserMessage/addAssistantMessage → appendMessageToChat() → saved
      //
      // 3️⃣  AUTO-GENERATED TITLES
      //     - First message triggers generateChatTitle()
      //     - Title = first 50 chars of first message
      //     - updateChatTitle() replaces "New Conversation"
      //
      // 4️⃣  SIDEBAR = UI HISTORY ONLY
      //     - Lists all past conversations
      //     - Grouped: Today, Yesterday, Previous 7 Days, Older
      //     - Only ONE chat marked active at a time
      //     - Click to reopen and view past messages
      //
      // 5️⃣  PERSISTENCE
      //     - Chat metadata: /get_conversations (backend ConversationModel)
      //     - Messages per chat: /get_messages/<conversation_id>
      //     - Survives page refresh and new browser sessions via DB
      //
      // USAGE:
      //   - Start new chat: startNewChat()
      //   - Load existing: loadChat(chatId)
      //   - Send message: sendMessage() → auto-generates title → appendMessageToChat()
      //   - Reopen chat: Click sidebar item → loadChat() → loads stored messages
      // ================================

      // Load chat history metadata from backend (mirrors chat.html loadChatHistory)
      async function loadChatHistoryFromBackend() {
        try {
          const response = await fetch('/get_conversations', {
            method: 'GET',
            credentials: 'include'
          });
          if (response.ok) {
            const data = await response.json();
            chatHistory = data.conversations || [];
            renderChatSidebar();
          } else {
            console.error('Failed to load chat history from backend');
            chatHistory = [];
            renderChatSidebar();
          }
        } catch (e) {
          console.error('Error loading chat history from backend', e);
          chatHistory = [];
          renderChatSidebar();
        }
      }

      // Backwards-compat stub: some helpers still call saveChatThreads().
      // Backend is now the source of truth for conversations, so this is a no-op.
      function saveChatThreads() {
        // Intentionally left blank – conversations come from /get_conversations.
      }

      // Render chat sidebar dynamically from chatHistory
      // ════════════════════════════════════════════════════════════════════════════════
      // SPECIFICATION #4: SIDEBAR PERSISTENCE & NAVIGATION
      // Sidebar is UI-ONLY navigation to past conversations
      // Contains: id, title, timestamp, active status
      // Does NOT contain context or memory
      // Persists in localStorage['teacher_chat_threads']
      // Survives page refresh and browser restart
      // ════════════════════════════════════════════════════════════════════════════════
      function renderChatSidebar() {
        // Group chats by time
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000);
        const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);

        const todayChats = [];
        const yesterdayChats = [];
        const weekChats = [];
        const olderChats = [];

        chatHistory.forEach(chat => {
          // Backend conversations may expose different timestamp fields.
          // Prefer explicit timestamp, then updated_at, then created_at.
          const rawTs = chat.timestamp || chat.updated_at || chat.created_at || null;
          if (!rawTs) {
            olderChats.push(chat);
            return;
          }

          const chatDate = new Date(rawTs);
          if (isNaN(chatDate.getTime())) {
            olderChats.push(chat);
            return;
          }
          const chatDay = new Date(chatDate.getFullYear(), chatDate.getMonth(), chatDate.getDate());

          if (chatDay.getTime() === today.getTime()) {
            todayChats.push(chat);
          } else if (chatDay.getTime() === yesterday.getTime()) {
            yesterdayChats.push(chat);
          } else if (chatDay.getTime() > weekAgo.getTime()) {
            weekChats.push(chat);
          } else {
            olderChats.push(chat);
          }
        });

        // Helper to format time
        function formatTime(timestamp) {
          if (!timestamp) return '';
          const date = new Date(timestamp);
          if (isNaN(date.getTime())) return '';
          const now = new Date();

          // Same day: show time
          if (date.toDateString() === now.toDateString()) {
            return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
          }

          // Yesterday or within week: show day name or relative
          const diffDays = Math.floor((now - date) / (24 * 60 * 60 * 1000));
          if (diffDays === 1) return 'Yesterday';
          if (diffDays < 7) return `${diffDays} days ago`;

          return date.toLocaleDateString();
        }

        // Helper to create chat item HTML
        function sidebarChatTitle(rawTitle) {
          let title = (rawTitle || '').toString().trim();
          if (!title) title = 'New Conversation';
          if (/^chat\s*:/i.test(title)) {
            title = title.replace(/^chat\s*:/i, '').trim() || 'New Conversation';
          }
          return `Chat: ${title}`;
        }

        function createChatItemHTML(chat) {
          const isActive = chat.id === currentChatId ? 'active' : '';
          const snippet = chat.lastMessageSnippet ? escapeHtml(chat.lastMessageSnippet) : '';
          const unread = chat.unreadCount && chat.unreadCount > 0 ? `<span class="ml-2 inline-flex items-center justify-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-500 text-white">${chat.unreadCount}</span>` : '';
          return `
          <div class="chat-history-item ${isActive}" onclick="if (window.tdShowView) tdShowView('tutor'); loadChat('${chat.id}')">
            <div class="flex items-center justify-between">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <p class="text-sm text-gray-900 truncate flex-1">${escapeHtml(sidebarChatTitle(chat.title))}</p>
                  ${unread}
                </div>
                <p class="text-xs text-gray-500 truncate">${snippet || formatTime(chat.timestamp)}</p>
              </div>
              <div class="relative">
                <button onclick="toggleChatMenu(event, '${chat.id}')" class="text-gray-400 hover:text-gray-600 p-1">
                  <i class="fas fa-ellipsis-v"></i>
                </button>
              </div>
            </div>
          </div>
        `;
        }

        // Update "Today" section (Desktop)
        const todayContainer = document.getElementById('todayChats');
        if (todayChats.length > 0) {
          todayContainer.innerHTML = todayChats.map(createChatItemHTML).join('');
          todayContainer.parentElement.style.display = 'block';
        } else {
          todayContainer.parentElement.style.display = 'none';
        }

        // Update "Today" section (Mobile)
        const todayContainerMobile = document.getElementById('todayChats-mobile');
        if (todayContainerMobile) {
          if (todayChats.length > 0) {
            todayContainerMobile.innerHTML = todayChats.map(createChatItemHTML).join('');
            todayContainerMobile.parentElement.style.display = 'block';
          } else {
            todayContainerMobile.parentElement.style.display = 'none';
          }
        }

        // Update "Yesterday" section (Desktop)
        const yesterdayContainer = document.getElementById('yesterdayChats');
        if (yesterdayChats.length > 0) {
          yesterdayContainer.innerHTML = yesterdayChats.map(createChatItemHTML).join('');
          yesterdayContainer.parentElement.style.display = 'block';
        } else {
          yesterdayContainer.parentElement.style.display = 'none';
        }

        // Update "Yesterday" section (Mobile)
        const yesterdayContainerMobile = document.getElementById('yesterdayChats-mobile');
        if (yesterdayContainerMobile) {
          if (yesterdayChats.length > 0) {
            yesterdayContainerMobile.innerHTML = yesterdayChats.map(createChatItemHTML).join('');
            yesterdayContainerMobile.parentElement.style.display = 'block';
          } else {
            yesterdayContainerMobile.parentElement.style.display = 'none';
          }
        }

        // Update "Previous 7 Days" section (Desktop)
        const weekContainer = document.getElementById('weekChats');
        if (weekChats.length > 0) {
          weekContainer.innerHTML = weekChats.map(createChatItemHTML).join('');
          weekContainer.parentElement.style.display = 'block';
        } else {
          weekContainer.parentElement.style.display = 'none';
        }

        // Update "Previous 7 Days" section (Mobile)
        const weekContainerMobile = document.getElementById('weekChats-mobile');
        if (weekContainerMobile) {
          if (weekChats.length > 0) {
            weekContainerMobile.innerHTML = weekChats.map(createChatItemHTML).join('');
            weekContainerMobile.parentElement.style.display = 'block';
          } else {
            weekContainerMobile.parentElement.style.display = 'none';
          }
        }

        // Update "Older" section (optional - for older chats) (Desktop)
        if (olderChats.length > 0) {
          // Find or create older section
          let olderSection = document.getElementById('olderChats');
          if (!olderSection) {
            const chatHistoryDiv = document.getElementById('chatHistoryContainer');
            if (chatHistoryDiv) {
              const olderDiv = document.createElement('div');
              olderDiv.className = 'mb-4';
              olderDiv.innerHTML = `
              <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Older</h3>
              <div id="olderChats" class="space-y-1"></div>
            `;
              chatHistoryDiv.appendChild(olderDiv);
              olderSection = document.getElementById('olderChats');
            }
          }
          if (olderSection) {
            olderSection.innerHTML = olderChats.map(createChatItemHTML).join('');
          }
        }

        // Update "Older" section (Mobile)
        if (olderChats.length > 0) {
          let olderSectionMobile = document.getElementById('olderChats-mobile');
          if (!olderSectionMobile) {
            const chatHistoryDivMobile = document.getElementById('chatHistoryContainer-mobile');
            if (chatHistoryDivMobile) {
              const olderDivMobile = document.createElement('div');
              olderDivMobile.className = 'mb-4';
              olderDivMobile.innerHTML = `
              <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Older</h3>
              <div id="olderChats-mobile" class="space-y-1"></div>
            `;
              chatHistoryDivMobile.appendChild(olderDivMobile);
              olderSectionMobile = document.getElementById('olderChats-mobile');
            }
          }
          if (olderSectionMobile) {
            olderSectionMobile.innerHTML = olderChats.map(createChatItemHTML).join('');
          }
        }
      }

      // Auto-resize textarea
      function autoResizeTextarea(textarea) {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
      }

      // Request in flight: block second message and keep Send disabled until response is received
      window.teacherChatRequestInFlight = false;

      // Update send button state (disabled when waiting for response or when input is empty)
      function updateSendButton() {
        const input = document.getElementById('messageInput');
        const sendBtn = document.getElementById('sendBtn');
        const hasText = input.value.trim().length > 0;
        sendBtn.disabled = window.teacherChatRequestInFlight || !hasText;
      }

      // Handle keyboard shortcuts (do not send if a request is already in flight)
      function handleKeyDown(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          if (!event.repeat && !window.teacherChatRequestInFlight && document.getElementById('messageInput').value.trim()) {
            sendMessage();
          }
        }
      }

      // Toggle chat history dropdown (top toolbar)
      function toggleChatHistoryDropdown(event) {
        if (event) event.stopPropagation();
        const dropdown = document.getElementById('chatHistoryDropdown');
        const chev = document.getElementById('chatHistoryChevron');
        if (!dropdown) return;
        const isOpen = dropdown.classList.contains('show');
        closeAllDropdowns();
        if (!isOpen) {
          dropdown.classList.add('show');
          if (chev) chev.classList.add('open');
        } else if (chev) {
          chev.classList.remove('open');
        }
      }

      // Toggle chat menu
      function toggleChatMenu(event, chatId) {
        event.stopPropagation();
        currentChatMenuId = chatId;

        const dropdown = document.getElementById('chatMenuDropdown');
        const rect = event.target.closest('button').getBoundingClientRect();
        const menuSize = 260; // matches .ccm-menu width in CSS

        let left = rect.left + rect.width / 2 - menuSize / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - menuSize - 8));
        let top = rect.bottom + 14;
        if (top + menuSize > window.innerHeight - 8) {
          top = Math.max(8, rect.top - menuSize - 14);
        }

        dropdown.style.left = left + 'px';
        dropdown.style.top = top + 'px';
        dropdown.classList.toggle('show');
      }

      // Close all dropdowns
      function closeAllDropdowns() {
        document.querySelectorAll('.dropdown-menu').forEach(dropdown => {
          dropdown.classList.remove('show');
        });
        const chev = document.getElementById('chatHistoryChevron');
        if (chev) chev.classList.remove('open');
      }

      // Show Create Lesson Wizard (2-step process)
      function showCreateLessonWizard() {
        if (typeof window.tdOpenCreateLesson === 'function') return window.tdOpenCreateLesson();
        return _legacyShowCreateLessonWizard();
      }

      function _legacyShowCreateLessonWizard() {
        console.log('showCreateLessonWizard called');

        // Prevent body scrolling when modal opens
        document.body.style.overflow = 'hidden';
        document.body.style.position = 'fixed';
        document.body.style.width = '100%';

        // Step 1: Upload PDF — matches the "Upload Your PDF" design provided for the create-lesson wizard
        const modalHtml = `
        <div id="createLessonModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-start md:items-center justify-center z-[9999] overflow-y-auto py-2 md:py-4">
            <div id="createLessonModalContent" class="bg-white rounded-2xl md:rounded-3xl w-full max-w-5xl mx-2 md:mx-4 my-auto shadow-2xl overflow-hidden">
                <!-- Header: matches new_ui/processing_upload_pdfui/17-processing-pdf.html -->
                <div class="ppm-header">
                  <div class="tt-logo">${window.TEACHER_CFG.logoDarkOnLight}</div>
                  <div class="ppm-close-x" onclick="closeCreateLessonModal()">
                    <i class="fas fa-times"></i>
                  </div>
                </div>

                <div class="pu1-body">
                  <!-- Left column: decorative upload visual + dropzone + step tracker -->
                  <div>
                    <div class="pu1-upload-card">
                      <div class="pu1-doc-visual">
                        <div class="pu1-doc-circle"><i class="fas fa-file-alt pu1-doc-icon"></i></div>
                        <div class="pu1-pdf-chip">PDF</div>
                        <div class="pu1-upload-badge"><i class="fas fa-arrow-up"></i></div>
                      </div>
                      <h2>Upload Your PDF</h2>
                      <p class="pu1-upload-sub">Drag &amp; drop your PDF file here<br>or <b>click to browse</b></p>

                      <div id="dropZone" class="pu1-dropzone">
                          <i class="fas fa-file-alt pu1-dropzone-icon"></i>
                          <p class="pu1-dropzone-title">Your PDF file</p>
                          <p class="pu1-dropzone-sub">(up to 100MB)</p>
                          <div class="pu1-format-badge"><i class="fas fa-check-circle"></i>PDF Format Supported</div>
                      </div>
                      <input type="file" id="pdfFileInput" accept=".pdf" class="hidden">
                      <div id="fileError" class="text-red-600 text-xs md:text-sm mt-3 hidden flex items-center gap-2 bg-red-50 p-2 md:p-3 rounded-lg border border-red-200 text-left">
                          <i class="fas fa-exclamation-triangle flex-shrink-0 text-sm md:text-base"></i>
                          <span id="fileErrorText" class="font-medium"></span>
                      </div>
                      <!-- Selected File Display -->
                      <div id="selectedFileDisplay" class="mt-3 md:mt-4 hidden">
                          <div class="bg-gradient-to-r from-green-50 to-emerald-50 border-2 border-green-300 rounded-lg md:rounded-xl p-3 md:p-4 flex items-center gap-3 shadow-sm text-left">
                              <div class="text-green-600 text-2xl flex-shrink-0">
                                  <i class="fas fa-check-circle"></i>
                              </div>
                              <div class="flex-1 text-left min-w-0">
                                  <p class="font-bold text-green-900 text-xs md:text-sm truncate" id="selectedFileName"></p>
                                  <p class="text-xs text-green-700 mt-0.5" id="selectedFileSize"></p>
                              </div>
                              <button type="button" onclick="clearFileSelection()" class="text-green-600 hover:text-green-800 hover:bg-green-100 p-1.5 md:p-2 rounded-lg transition-all flex-shrink-0">
                                  <i class="fas fa-times text-sm md:text-lg"></i>
                              </button>
                          </div>
                      </div>
                    </div>

                    <!-- Step tracker: Upload -> Process -->
                    <div class="pu1-tracker-card">
                      <div class="pu1-tracker-row">
                        <div class="pu1-tracker-step active">
                          <div class="pu1-tracker-circle"><i class="fas fa-cloud-upload-alt"></i></div>
                          <h4>Upload</h4>
                          <p>Select &amp; validate</p>
                        </div>
                        <div class="pu1-tracker-arrow"><span class="pu1-arrow-line"></span><i class="fas fa-chevron-right"></i></div>
                        <div class="pu1-tracker-step pending">
                          <div class="pu1-tracker-circle"><i class="fas fa-brain"></i></div>
                          <h4>Process</h4>
                          <p>AI analysis</p>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- Right column: lesson details form -->
                  <div class="pu1-right">
                    <div class="pu1-step-ribbon">STEP 1 OF 2</div>
                    <div class="pu1-right-body">
                      <!-- Lesson Title -->
                      <div class="pu1-field">
                          <div class="pu1-field-label">
                            <span class="pu1-field-icon"><i class="fas fa-heading"></i></span>
                            <span>Lesson Title <span class="text-red-500">*</span></span>
                          </div>
                          <input type="text" id="lessonTitle"
                                 class="pu1-input border-2 border-emerald-600 focus:border-emerald-700"
                                 placeholder="e.g., 'Introduction to Algebra'"
                                 oninput="clearLessonTitleFormError()"
                                 required>
                          <div id="lessonTitleError" class="text-red-600 text-xs md:text-sm mt-2 hidden flex items-start gap-2 bg-red-50 p-2 md:p-3 rounded-lg border border-red-200" role="alert">
                              <i class="fas fa-exclamation-circle flex-shrink-0 mt-0.5 text-sm md:text-base"></i>
                              <span id="lessonTitleErrorText" class="font-medium"></span>
                          </div>
                      </div>

                      <!-- Subject and Grade Row -->
                      <div class="pu1-row2">
                          <div class="pu1-field">
                              <div class="pu1-field-label">
                                <span class="pu1-field-icon"><i class="fas fa-book"></i></span>
                                <span>Subject</span>
                              </div>
                              <select id="lessonSubject" class="pu1-input border-2 border-emerald-600 focus:border-emerald-700">
                                  <option value="General">General</option>
                                  <option value="Math">Mathematics</option>
                                  <option value="Science">Science</option>
                                  <option value="English">English</option>
                                  <option value="History">History</option>
                                  <option value="Biology">Biology</option>
                                  <option value="Chemistry">Chemistry</option>
                                  <option value="Physics">Physics</option>
                              </select>
                          </div>
                          <div class="pu1-field">
                              <div class="pu1-field-label">
                                <span class="pu1-field-icon"><i class="fas fa-graduation-cap"></i></span>
                                <span>Grade Level <span class="text-red-500">*</span></span>
                              </div>
                              <select id="lessonGrade"
                                      class="pu1-input border-2 border-emerald-600 focus:border-emerald-700"
                                      onchange="clearLessonGradeFormError()"
                                      required>
                                  <option value="">Select grade</option>
                                  <option value="1">1st Grade</option>
                                  <option value="2">2nd Grade</option>
                                  <option value="3">3rd Grade</option>
                                  <option value="4">4th Grade</option>
                                  <option value="5">5th Grade</option>
                                  <option value="6">6th Grade</option>
                                  <option value="7">7th Grade</option>
                                  <option value="8">8th Grade</option>
                                  <option value="9">9th Grade</option>
                                  <option value="10">10th Grade</option>
                                  <option value="11">11th Grade</option>
                                  <option value="12">12th Grade</option>
                              </select>
                              <div id="lessonGradeError" class="text-red-600 text-xs md:text-sm mt-2 hidden flex items-start gap-2 bg-red-50 p-2 md:p-3 rounded-lg border border-red-200" role="alert">
                                  <i class="fas fa-exclamation-circle flex-shrink-0 mt-0.5 text-sm md:text-base"></i>
                                  <span id="lessonGradeErrorText" class="font-medium"></span>
                              </div>
                          </div>
                      </div>

                      <!-- Context/Prompt -->
                      <div class="pu1-field">
                          <div class="pu1-field-label">
                            <span class="pu1-field-icon"><i class="fas fa-comment-dots"></i></span>
                            <span>Lesson Context <span class="text-gray-400 font-normal text-xs">(Optional)</span></span>
                          </div>
                          <div class="pu1-context-wrap">
                            <textarea id="lessonContext"
                                     class="pu1-input border-2 border-emerald-600 focus:border-emerald-700"
                                     rows="3" maxlength="500"
                                     oninput="document.getElementById('lessonContextCount').textContent = this.value.length"
                                     placeholder="Describe the lesson content, target audience, or any specific requirements..."></textarea>
                            <span class="pu1-char-count"><span id="lessonContextCount">0</span>/500</span>
                          </div>
                      </div>

                      <div class="pu1-tip-box">
                        <i class="fas fa-lightbulb"></i>
                        <p>Provide a clear context to help IQBAL AI create more accurate and effective lessons.</p>
                      </div>

                      <div class="pu1-field">
                          <div class="pu1-field-label">
                            <span class="pu1-field-icon"><i class="fas fa-sliders-h"></i></span>
                            <span>Lesson Output</span>
                          </div>
                          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                            <label class="flex items-start gap-3 border border-emerald-200 rounded-lg p-3 cursor-pointer bg-white">
                              <input type="radio" name="lessonOutputMode" value="generate" class="mt-1" checked>
                              <span>
                                <span class="block font-semibold text-gray-800">Generate from PDF</span>
                                <span class="block text-xs text-gray-500">Use chat to create an AI lesson from the uploaded PDF.</span>
                              </span>
                            </label>
                            <label class="flex items-start gap-3 border border-emerald-200 rounded-lg p-3 cursor-pointer bg-white">
                              <input type="radio" name="lessonOutputMode" value="as_is" class="mt-1">
                              <span>
                                <span class="block font-semibold text-gray-800">Use PDF as lesson</span>
                                <span class="block text-xs text-gray-500">Show the uploaded document text directly as the lesson.</span>
                              </span>
                            </label>
                          </div>
                      </div>

                      <!-- Action Buttons -->
                      <button id="nextStepButton" onclick="processCreateLessonStep1()" class="pu1-submit-btn">
                          <i class="fas fa-arrow-up"></i>
                          Upload &amp; Process
                      </button>
                      <button onclick="closeCreateLessonModal()" class="pu1-cancel-btn2">
                          Cancel
                      </button>
                    </div>
                  </div>
                </div>
            </div>
        </div>
      `;

        // Remove existing modal if any
        const existingModal = document.getElementById('createLessonModal');
        if (existingModal) {
          existingModal.remove();
        }

        // Add modal to document
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Setup file input functionality
        setTimeout(() => {
          setupFileUpload();
        }, 100);
      }

      // Helper function to close the modal
      function closeCreateLessonModal() {
        window._ingestTaskId = null;
        window._ingestAbortController = null;
        window._ingestCancelRequested = false;
        window._ingestCompletedSuccessfully = false;
        const modal = document.getElementById('createLessonModal');
        if (modal) {
          modal.remove();
        }

        // Restore body scrolling
        document.body.style.overflow = '';
        document.body.style.position = '';
        document.body.style.width = '';

        // Only show chat input area if Chat tab is currently active
        const chatTabBtn = document.getElementById('chatTabBtn');
        const chatInputArea = document.getElementById('chatInputArea');

        if (chatTabBtn && chatTabBtn.classList.contains('active-tab') && chatInputArea) {
          chatInputArea.style.display = 'block';

          // Use requestAnimationFrame to ensure browser layout calculations are done
          requestAnimationFrame(() => {
            setTimeout(() => {
              scrollToBottom();
              const input = document.getElementById('messageInput');
              if (input) input.focus();
            }, 10);
          });
        }
      }

      // Setup file upload functionality
      function setupFileUpload() {
        const fileInput = document.getElementById('pdfFileInput');
        const dropZone = document.getElementById('dropZone');
        const fileError = document.getElementById('fileError');
        const fileErrorText = document.getElementById('fileErrorText');
        const selectedFileDisplay = document.getElementById('selectedFileDisplay');
        const selectedFileName = document.getElementById('selectedFileName');
        const selectedFileSize = document.getElementById('selectedFileSize');

        if (!fileInput || !dropZone) {
          console.error('File input or drop zone not found. Retrying...');
          // Retry after another 100ms
          setTimeout(() => setupFileUpload(), 100);
          return;
        }

        // Click on drop zone to trigger file input
        dropZone.addEventListener('click', function () {
          fileInput.click();
        });

        // Handle file selection
        fileInput.addEventListener('change', function (e) {
          const file = this.files[0];
          if (file) {
            // Validate file size (100MB max)
            const maxSize = 100 * 1024 * 1024; // 100MB in bytes
            if (file.size > maxSize) {
              fileErrorText.textContent = 'File size exceeds 100MB limit. Please upload a smaller file.';
              fileError.classList.remove('hidden');
              selectedFileDisplay?.classList.add('hidden');
              return;
            }

            // Validate file type
            if (!file.type.includes('pdf')) {
              fileErrorText.textContent = 'Please upload a PDF file only.';
              fileError.classList.remove('hidden');
              selectedFileDisplay?.classList.add('hidden');
              return;
            }

            // Valid file
            fileError.classList.add('hidden');
            const fileSize = (file.size / (1024 * 1024)).toFixed(2);

            // Update selected file display
            if (selectedFileDisplay && selectedFileName && selectedFileSize) {
              selectedFileName.textContent = file.name;
              selectedFileSize.textContent = `${fileSize} MB`;
              selectedFileDisplay.classList.remove('hidden');
            }
          }
        });

        // Add drag and drop functionality
        dropZone.addEventListener('dragover', function (e) {
          e.preventDefault();
          e.stopPropagation();
          this.classList.add('pu1-drag-over');
        });

        dropZone.addEventListener('dragleave', function (e) {
          e.preventDefault();
          e.stopPropagation();
          this.classList.remove('pu1-drag-over');
        });

        dropZone.addEventListener('drop', function (e) {
          e.preventDefault();
          e.stopPropagation();
          this.classList.remove('pu1-drag-over');

          const files = e.dataTransfer.files;
          if (files.length > 0) {
            fileInput.files = files;
            // Trigger change event
            const event = new Event('change');
            fileInput.dispatchEvent(event);
          }
        });
      }

      function clearLessonTitleFormError() {
        const wrap = document.getElementById('lessonTitleError');
        const textEl = document.getElementById('lessonTitleErrorText');
        const input = document.getElementById('lessonTitle');
        if (wrap) wrap.classList.add('hidden');
        if (textEl) textEl.textContent = '';
        if (input) {
          input.classList.remove('border-red-500', 'ring-2', 'ring-red-200');
          input.classList.add('border-emerald-600');
        }
      }

      function showLessonTitleFormError(message) {
        const wrap = document.getElementById('lessonTitleError');
        const textEl = document.getElementById('lessonTitleErrorText');
        const input = document.getElementById('lessonTitle');
        if (textEl) textEl.textContent = message;
        if (wrap) wrap.classList.remove('hidden');
        if (input) {
          input.classList.remove('border-emerald-600');
          input.classList.add('border-red-500', 'ring-2', 'ring-red-200');
        }
      }

      function clearLessonGradeFormError() {
        const wrap = document.getElementById('lessonGradeError');
        const textEl = document.getElementById('lessonGradeErrorText');
        const input = document.getElementById('lessonGrade');
        if (wrap) wrap.classList.add('hidden');
        if (textEl) textEl.textContent = '';
        if (input) {
          input.classList.remove('border-red-500', 'ring-2', 'ring-red-200');
          input.classList.add('border-emerald-600');
        }
      }

      function showLessonGradeFormError(message) {
        const wrap = document.getElementById('lessonGradeError');
        const textEl = document.getElementById('lessonGradeErrorText');
        const input = document.getElementById('lessonGrade');
        if (textEl) textEl.textContent = message;
        if (wrap) wrap.classList.remove('hidden');
        if (input) {
          input.classList.remove('border-emerald-600');
          input.classList.add('border-red-500', 'ring-2', 'ring-red-200');
        }
      }

      // Clear file selection
      function clearFileSelection() {
        const fileInput = document.getElementById('pdfFileInput');
        const selectedFileDisplay = document.getElementById('selectedFileDisplay');
        const fileError = document.getElementById('fileError');

        if (fileInput) fileInput.value = '';
        if (selectedFileDisplay) selectedFileDisplay.classList.add('hidden');
        if (fileError) fileError.classList.add('hidden');
      }

      // Step 1 processing with better validation
      async function processCreateLessonStep1() {
        console.log('processCreateLessonStep1 called');

        // Get elements directly from the document
        const lessonTitleInput = document.getElementById('lessonTitle');
        const lessonContextInput = document.getElementById('lessonContext');
        const lessonSubjectInput = document.getElementById('lessonSubject');
        const lessonGradeInput = document.getElementById('lessonGrade');
        const lessonModeInput = document.querySelector('input[name="lessonOutputMode"]:checked');
        const fileInput = document.getElementById('pdfFileInput');

        console.log('Form elements:', {
          lessonTitleInput: !!lessonTitleInput,
          lessonContextInput: !!lessonContextInput,
          lessonSubjectInput: !!lessonSubjectInput,
          lessonGradeInput: !!lessonGradeInput,
          fileInput: !!fileInput,
          hasFile: fileInput ? fileInput.files.length > 0 : false
        });

        // Validate elements exist
        if (!lessonTitleInput || !fileInput) {
          console.error('Required form elements not found');
          alert('Error: Form elements not found. Please refresh and try again.');
          return;
        }

        const lessonTitle = lessonTitleInput.value.trim();
        const lessonContext = lessonContextInput ? lessonContextInput.value.trim() : '';
        const lessonSubject = lessonSubjectInput ? lessonSubjectInput.value : 'General';
        const lessonGrade = lessonGradeInput ? String(lessonGradeInput.value || '').trim() : '';
        const lessonMode = lessonModeInput ? lessonModeInput.value : 'generate';
        const assignClassEl = document.getElementById('lessonAssignClass');
        window._pendingLessonAssignClassId = assignClassEl && assignClassEl.value
          ? String(assignClassEl.value)
          : '';

        // Validation
        clearLessonTitleFormError();
        clearLessonGradeFormError();

        if (!lessonTitle) {
          showLessonTitleFormError('Lesson title is required.');
          lessonTitleInput.focus();
          return;
        }

        if (!lessonGrade) {
          showLessonGradeFormError('Grade is required.');
          if (lessonGradeInput) lessonGradeInput.focus();
          return;
        }

        if (!fileInput.files.length) {
          alert('Please select a PDF file.');

          // Highlight the drop zone to indicate error
          const dropZone = document.getElementById('dropZone');
          if (dropZone) {
            dropZone.style.borderColor = '#ef4444';
            dropZone.style.backgroundColor = '#fef2f2';
            setTimeout(() => {
              dropZone.style.borderColor = '';
              dropZone.style.backgroundColor = '';
            }, 2000);
          }
          return;
        }

        const file = fileInput.files[0];

        // File validation
        const maxSize = 100 * 1024 * 1024; // 100MB
        if (file.size > maxSize) {
          alert('File size exceeds 100MB limit. Please upload a smaller file.');
          return;
        }

        if (!file.type.includes('pdf')) {
          alert('Please upload a PDF file only.');
          return;
        }

        try {
          const checkRes = await fetch(
            '/api/lessons/check_title_exists?' + new URLSearchParams({ title: lessonTitle }),
            { method: 'GET', credentials: 'include' }
          );
          const checkData = await checkRes.json().catch(function () { return {}; });
          if (checkRes.ok && checkData.exists) {
            showLessonTitleFormError('This title is already used by another lesson. Please choose a different title.');
            lessonTitleInput.focus();
            return;
          }
        } catch (e) {
          console.warn('Could not verify lesson title uniqueness:', e);
          showToast('Could not verify if this title is available. If the problem persists, check your connection.', 'warning', 3500);
        }

        console.log('All validations passed, proceeding to step 2');

        // Auto-progress to Step 2: Processing
        showCreateLessonStep2(file, lessonTitle, lessonContext, lessonSubject, lessonGrade, lessonMode);
      }

      // Step 2 with responsive design
      function showCreateLessonStep2(file, lessonTitle, lessonContext, lessonSubject, lessonGrade, lessonMode) {
        if (typeof window.tdEnsureCreateLessonProcessingModal === 'function') window.tdEnsureCreateLessonProcessingModal();
        const modalContent = document.getElementById('createLessonModalContent');
        if (!modalContent) return;

        const fileSizeMB = (file.size / (1024 * 1024)).toFixed(2);

        modalContent.innerHTML = `
        <!-- Header: matches new_ui/processing_upload_pdfui/17-processing-pdf.html -->
        <div class="ppm-header">
          <div class="tt-logo">${window.TEACHER_CFG.logoDarkOnLight}</div>
          <div class="ppm-close-x" onclick="closeCreateLessonModal()">
            <i class="fas fa-times"></i>
          </div>
        </div>

        <div class="ppm-body">
          <div class="ppm-top-row">
            <div class="ppm-top-left">
              <div class="ppm-step-label">Step 2 of 2</div>
              <h1>Processing PDF</h1>
              <div class="ppm-subtitle">Creating Lesson</div>
            </div>
          </div>

          <div class="ppm-main-grid">
            <!-- Left: file preview -->
            <div class="ppm-left-panel">
              <div class="ppm-file-preview">
                <svg viewBox="0 0 384 512" fill="var(--primary-color)"><path d="M369.9 97.9L286 14C277 5 264.8-.1 252.1-.1H48C21.5 0 0 21.5 0 48v416c0 26.5 21.5 48 48 48h288c26.5 0 48-21.5 48-48V131.9c0-12.7-5.1-25-14.1-34zM332.1 128H256V51.9l76.1 76.1zM48 464V48h160v104c0 13.3 10.7 24 24 24h104v288H48z"/></svg>
                <div class="ppm-upload-badge">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 19V5M5 12l7-7 7 7" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
                </div>
              </div>
              <div class="ppm-file-name">${file.name}</div>
              <div class="ppm-file-size">${fileSizeMB} MB</div>
              <div class="ppm-secure-box">
                <div class="ppm-shield">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 2 L20 5 V11 C20 16 16.5 20 12 22 C7.5 20 4 16 4 11 V5 Z" stroke="var(--primary-color)" stroke-width="1.8"/></svg>
                </div>
                <div>
                  <b>Your file is secure and private</b>
                  <span>We don't store your content</span>
                </div>
              </div>
            </div>

            <!-- Right: progress + steps -->
            <div class="ppm-right-panel">
              <div class="ppm-progress-head">
                <div class="ppm-progress-head-top">
                  <div class="ppm-progress-head-left">
                    <div class="ppm-wave-icon">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M3 12h2l2-6 3 12 3-9 2 4h6" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
                    </div>
                    <h2>Processing PDF...</h2>
                  </div>
                  <div class="ppm-progress-pct"><span id="progressPercent">0%</span></div>
                </div>
                <div class="ppm-progress-track"><div id="progressBar" class="ppm-progress-fill" style="width: 0%;"></div></div>
                <p id="progressStatus" class="ppm-progress-status-text" style="font-size:12.5px;color:#6b7280;margin-top:10px;">Starting PDF analysis...</p>
              </div>

              <div class="ppm-steps">
                <div class="ppm-step-row active" id="ppmStepRow1">
                  <div class="ppm-step-icon pending" id="ppmStepIcon1">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M5 13l4 4L19 7" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>
                  </div>
                  <div class="ppm-step-text">
                    <h4>File received</h4>
                    <p>PDF successfully uploaded</p>
                  </div>
                  <div class="ppm-step-status pending" id="ppmStepStatus1">Pending</div>
                </div>
                <div class="ppm-step-row" id="ppmStepRow2">
                  <div class="ppm-step-icon pending" id="ppmStepIcon2">
                    <i class="fas fa-circle-notch ppm-spin" style="font-size:13px;color:var(--green-500);"></i>
                  </div>
                  <div class="ppm-step-text">
                    <h4>Extracting text</h4>
                    <p>Reading content from PDF</p>
                  </div>
                  <div class="ppm-step-status pending" id="ppmStepStatus2">Pending</div>
                </div>
                <div class="ppm-step-row" id="ppmStepRow3">
                  <div class="ppm-step-icon pending" id="ppmStepIcon3">
                    <i class="fas fa-circle-notch ppm-spin" style="font-size:13px;color:var(--green-500);"></i>
                  </div>
                  <div class="ppm-step-text">
                    <h4>Indexing for chat</h4>
                    <p>Organizing content for AI</p>
                  </div>
                  <div class="ppm-step-status pending" id="ppmStepStatus3">Pending</div>
                </div>
                <div class="ppm-step-row" id="ppmStepRow4">
                  <div class="ppm-step-icon pending" id="ppmStepIcon4">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="#fff" stroke-width="1.8"/><path d="M8 11V7a4 4 0 018 0v4" stroke="#fff" stroke-width="1.8" fill="none"/></svg>
                  </div>
                  <div class="ppm-step-text">
                    <h4>Preparing response</h4>
                    <p>Almost there...</p>
                  </div>
                  <div class="ppm-step-status pending" id="ppmStepStatus4">Pending</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="ppm-bottom-bar">
          <div class="ppm-bottom-left">
            <div class="ppm-bot-icon">
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none"><rect x="4" y="8" width="16" height="12" rx="3" fill="#fff"/><circle cx="9" cy="14" r="1.4" fill="#0f1115"/><circle cx="15" cy="14" r="1.4" fill="#0f1115"/><path d="M12 8V4M8 4l-2-2M16 4l2-2" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>
            </div>
            <div>
              <h3>IQBAL AI is analyzing your content</h3>
              <p>This may take a few moments</p>
              <div class="ppm-dots"><span class="on"></span><span></span><span></span></div>
            </div>
          </div>
          <button onclick="cancelCreateLessonUpload()" class="ppm-cancel-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="var(--primary-color)" stroke-width="1.8"/><path d="M5.5 5.5l13 13" stroke="var(--primary-color)" stroke-width="1.8"/></svg>
            Cancel Processing
          </button>
        </div>
      `;

        window._createLessonFile = file;
        window._createLessonMeta = { lessonTitle, lessonContext, lessonSubject, lessonGrade, lessonMode, conversationId: null, threadId: null };
        setTimeout(() => {
          processCreateLessonStep2(file.name, file.size, lessonTitle, lessonContext, lessonSubject, lessonGrade, lessonMode);
        }, 500);
      }

      // Graceful cancel for create-lesson upload: abort in-flight request or revoke Celery task, then close modal
      function cancelCreateLessonUpload() {
        window._ingestCancelRequested = true;
        const cancelPayload = {
          thread_id: window._ingestPendingThreadId || null
        };
        if (window._ingestTaskId) {
          fetch('/api/rag/ingest/cancel/' + window._ingestTaskId, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cancelPayload)
          }).catch(function() {});
        }
        if (window._ingestAbortController) {
          window._ingestAbortController.abort();
        }
        // Preserve the user's currently opened chat/thread after cancellation.
        if (Object.prototype.hasOwnProperty.call(window, '_ingestPrevThreadId')) {
          window.currentRAGThreadId = window._ingestPrevThreadId || null;
          try {
            if (window.currentRAGThreadId) localStorage.setItem('teacher_currentRAGThreadId', window.currentRAGThreadId);
            else localStorage.removeItem('teacher_currentRAGThreadId');
          } catch (e) {}
        }
        if (Object.prototype.hasOwnProperty.call(window, '_ingestPrevConversationId')) {
          window.currentRAGConversationId = window._ingestPrevConversationId != null ? window._ingestPrevConversationId : null;
          try {
            if (window.currentRAGConversationId != null) localStorage.setItem('teacher_currentRAGConversationId', String(window.currentRAGConversationId));
            else localStorage.removeItem('teacher_currentRAGConversationId');
          } catch (e) {}
        }
        closeCreateLessonModal();
      }

      // Drive the 4-step "File received / Extracting text / Indexing for chat / Preparing response"
      // list (new_ui/processing_upload_pdfui/17-processing-pdf.html) off the single 0-100 progress number.
      function updatePpmSteps(progress) {
        const p = Math.max(0, Math.min(100, progress || 0));
        const steps = [
          { icon: 'ppmStepIcon1', status: 'ppmStepStatus1', row: 'ppmStepRow1', doneAt: 2 },
          { icon: 'ppmStepIcon2', status: 'ppmStepStatus2', row: 'ppmStepRow2', doneAt: 45 },
          { icon: 'ppmStepIcon3', status: 'ppmStepStatus3', row: 'ppmStepRow3', doneAt: 85 },
          { icon: 'ppmStepIcon4', status: 'ppmStepStatus4', row: 'ppmStepRow4', doneAt: 100 },
        ];
        let activeIndex = steps.findIndex(function (s) { return p < s.doneAt; });
        if (activeIndex === -1) activeIndex = steps.length - 1;
        steps.forEach(function (step, idx) {
          const iconEl = document.getElementById(step.icon);
          const statusEl = document.getElementById(step.status);
          const rowEl = document.getElementById(step.row);
          if (!iconEl || !statusEl || !rowEl) return;
          const isDone = idx < activeIndex || (idx === steps.length - 1 && p >= 100);
          const isActive = !isDone && idx === activeIndex;
          iconEl.classList.remove('done', 'progress', 'pending');
          statusEl.classList.remove('completed', 'inprogress', 'pending');
          rowEl.classList.toggle('active', isActive);
          if (isDone) {
            iconEl.classList.add('done');
            statusEl.classList.add('completed');
            statusEl.textContent = 'Completed';
          } else if (isActive) {
            iconEl.classList.add('progress');
            statusEl.classList.add('inprogress');
            statusEl.textContent = 'In progress';
          } else {
            iconEl.classList.add('pending');
            statusEl.classList.add('pending');
            statusEl.textContent = 'Pending';
          }
        });
      }

      // Step 2 processing - API: POST /api/rag/ingest (RAG document ingest for chat), NOT create_lesson
      async function processCreateLessonStep2(fileName, fileSize, lessonTitle, lessonContext, lessonSubject, lessonGrade, lessonMode) {
        const modal = document.getElementById('createLessonModal');
        if (!modal) return;
        const progressBar = modal.querySelector('#progressBar');
        const progressPercent = modal.querySelector('#progressPercent');
        const progressStatus = modal.querySelector('#progressStatus');
        const file = window._createLessonFile;
        if (!file) {
          showToast('File missing. Please start again.', 'error');
          closeCreateLessonModal();
          return;
        }
        // Snapshot currently opened chat so cancel never hijacks user context.
        window._ingestPrevThreadId = window.currentRAGThreadId || null;
        window._ingestPrevConversationId = window.currentRAGConversationId != null ? window.currentRAGConversationId : null;
        window._ingestAbortController = new AbortController();
        window._ingestTaskId = null;
        window._ingestCancelRequested = false;
        window._ingestCompletedSuccessfully = false;
        window._ingestPendingThreadId = null;
        let pendingThreadId = null;
        let pendingConversationId = null;
        const formData = new FormData();
        formData.append('file', file);
        formData.append('create_new_thread', 'true');
        // Do NOT send conversation_id: each upload gets a new conversation and thread (isolated instance)
        let progress = 0;
        updatePpmSteps(0);
        const progressInterval = setInterval(() => {
          progress += Math.random() * 8 + 2;
          if (progress > 90) progress = 90;
          if (progressBar) progressBar.style.width = progress + '%';
          if (progressPercent) progressPercent.textContent = Math.round(progress) + '%';
          if (progressStatus) {
            if (progress < 40) progressStatus.textContent = 'Uploading PDF...';
            else if (progress < 80) progressStatus.textContent = 'Extracting text and indexing for chat...';
            else progressStatus.textContent = 'Finalizing...';
          }
          updatePpmSteps(progress);
        }, 400);
        try {
          const response = await fetch('/api/rag/ingest', {
            method: 'POST',
            body: formData,
            credentials: 'include',
            signal: window._ingestAbortController.signal
          });
          clearInterval(progressInterval);
          if (progressBar) progressBar.style.width = '100%';
          if (progressPercent) progressPercent.textContent = '100%';
          if (progressStatus) progressStatus.textContent = 'Finalizing...';
          if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showToast(err.error || 'PDF ingest failed', 'error');
            closeCreateLessonModal();
            return;
          }
          const data = await response.json().catch(() => ({}));
          let threadId = data.thread_id || null;
          let conversationId = data.conversation_id != null ? data.conversation_id : null;
          pendingThreadId = threadId;
          pendingConversationId = conversationId;
          window._ingestPendingThreadId = pendingThreadId;
          if (data.task_id && data.status === 'processing') {
            window._ingestTaskId = data.task_id;
            if (progressStatus) progressStatus.textContent = 'Processing in background...';
            updatePpmSteps(45);
            threadId = data.thread_id || null;
            conversationId = data.conversation_id != null ? data.conversation_id : null;
            pendingThreadId = threadId;
            pendingConversationId = conversationId;
            window._ingestPendingThreadId = pendingThreadId;
            const taskId = data.task_id;
            const pollMax = 120;
            for (let i = 0; i < pollMax; i++) {
              if (window._ingestCancelRequested) {
                showToast('Upload cancelled.', 'warning');
                break;
              }
              await new Promise(r => setTimeout(r, 2000));
              if (window._ingestCancelRequested) {
                showToast('Upload cancelled.', 'warning');
                break;
              }
              const statusRes = await fetch('/api/rag/ingest/status/' + taskId, { method: 'GET', credentials: 'include' });
              if (!statusRes.ok) continue;
              const statusData = await statusRes.json().catch(() => ({}));
              if (progressBar) progressBar.style.width = (statusData.progress || 0) + '%';
              if (progressPercent) progressPercent.textContent = Math.round(statusData.progress || 0) + '%';
              if (progressStatus && statusData.message) progressStatus.textContent = statusData.message;
              updatePpmSteps(statusData.progress || 0);
              if (statusData.status === 'revoked') {
                showToast('Upload cancelled.', 'warning');
                break;
              }
              if (statusData.status === 'success') {
                threadId = statusData.thread_id || threadId;
                conversationId = statusData.conversation_id != null ? statusData.conversation_id : conversationId;
                pendingThreadId = threadId;
                pendingConversationId = conversationId;
                window._ingestPendingThreadId = pendingThreadId;
                window._ingestCompletedSuccessfully = true;
                if (statusData.warning) {
                  showToast(statusData.warning, 'warning');
                }
                if (!window._createLessonMeta) window._createLessonMeta = {};
                window._createLessonMeta.numPages = statusData.num_pages || statusData.pages || statusData.documents || null;
                break;
              }
              if (statusData.status === 'failed' || statusData.status === 'failure' || statusData.state === 'FAILURE') {
                showToast(statusData.error || statusData.message || 'PDF processing failed', 'error');
                closeCreateLessonModal();
                return;
              }
            }
          }
          const ingestSuccess = !window._ingestCancelRequested && (window._ingestCompletedSuccessfully || (!data.task_id && threadId)) && threadId;
          if (ingestSuccess) updatePpmSteps(100);

          // Commit new thread/conversation only after confirmed success.
          if (ingestSuccess) {
            if (threadId) {
              window.currentRAGThreadId = threadId;
              try { localStorage.setItem('teacher_currentRAGThreadId', threadId); } catch (e) {}
            }
            if (conversationId != null) {
              window.currentRAGConversationId = conversationId;
              try { localStorage.setItem('teacher_currentRAGConversationId', String(conversationId)); } catch (e) {}
              if (!window._createLessonMeta) window._createLessonMeta = {};
              window._createLessonMeta.conversationId = conversationId;
              window._createLessonMeta.threadId = threadId;
            }
            if (!window._createLessonMeta) window._createLessonMeta = {};
            if (window._createLessonMeta.numPages == null) {
              window._createLessonMeta.numPages = data.num_pages || data.pages || data.documents || null;
            }
            // Auto-redirect into chat after successful processing (no manual Continue click).
            finalizeCreateLesson(fileName, fileSize, lessonTitle, lessonContext, lessonSubject, lessonGrade, lessonMode);
          } else {
            // Best-effort cleanup for cancelled thread so it cannot be used accidentally.
            if (window._ingestCancelRequested && pendingThreadId) {
              fetch('/api/rag/thread/' + encodeURIComponent(pendingThreadId), {
                method: 'DELETE',
                credentials: 'include'
              }).catch(function() {});
            }
            closeCreateLessonModal();
          }
          window._ingestPendingThreadId = null;
        } catch (e) {
          clearInterval(progressInterval);
          const isAbort = e && (e.name === 'AbortError' || e.message === 'The user aborted a request.');
          if (!isAbort) showToast('Network error. Please try again.', 'error');
          window._ingestPendingThreadId = null;
          closeCreateLessonModal();
        }
      }

      // Complete the two-step upload workflow without creating a separate completion step.
      function showCreateLessonUploadComplete(fileName, fileSize, lessonTitle, lessonContext, lessonSubject, lessonGrade) {
        const modalContent = document.getElementById('createLessonModalContent');
        if (!modalContent) return;

        modalContent.innerHTML = `
        <div class="bg-gradient-to-r from-primary-600 via-primary-500 to-accent-500 text-white px-6 md:px-10 py-8 md:py-10 shadow-lg">
            <div class="flex items-start justify-between gap-4">
                <div>
                    <h2 class="text-3xl md:text-4xl font-bold text-white mb-2">Creating Lesson</h2>
                    <p class="text-lg text-primary-100 font-medium opacity-90">Step 2 of 2: Processing PDF</p>
                </div>
                <button onclick="closeCreateLessonModal()" 
                        class="text-white hover:text-gray-200 hover:bg-white hover:bg-opacity-20 rounded-full p-3 transition-all flex-shrink-0">
                    <i class="fas fa-times text-2xl"></i>
                </button>
            </div>
        </div>

        <div class="bg-gray-50 px-6 md:px-10 py-8">
            <div class="flex items-center justify-between">
                <div class="flex flex-col items-center flex-1">
                    <div class="relative mb-4">
                        <div class="w-14 h-14 md:w-16 md:h-16 rounded-full bg-success-500 text-white flex items-center justify-center font-bold text-xl md:text-2xl shadow-lg ring-4 ring-success-100">
                            <i class="fas fa-check"></i>
                        </div>
                        <div class="absolute -bottom-1 -right-1 w-6 h-6 bg-success-500 rounded-full flex items-center justify-center text-white text-xs font-bold">1</div>
                    </div>
                    <h3 class="text-sm md:text-base font-bold text-success-700 text-center">Uploaded</h3>
                    <p class="text-xs text-gray-600 text-center mt-1">PDF loaded</p>
                </div>
                
                <div class="flex-1 h-1 bg-success-500 mx-2 md:mx-3 mb-6"></div>
                
                <div class="flex flex-col items-center flex-1">
                    <div class="relative mb-4">
                        <div class="w-14 h-14 md:w-16 md:h-16 rounded-full bg-success-500 text-white flex items-center justify-center font-bold text-xl md:text-2xl shadow-lg ring-4 ring-success-100">
                            <i class="fas fa-check"></i>
                        </div>
                        <div class="absolute -bottom-1 -right-1 w-6 h-6 bg-success-500 rounded-full flex items-center justify-center text-white text-xs font-bold">2</div>
                    </div>
                    <h3 class="text-sm md:text-base font-bold text-success-700 text-center">Processed</h3>
                    <p class="text-xs text-gray-600 text-center mt-1">AI analysis</p>
                </div>
            </div>
        </div>
        
        <div class="px-6 md:px-10 py-8">
            <div class="text-center">
                <div class="w-20 h-20 md:w-24 md:h-24 mx-auto mb-6 bg-gradient-to-br from-success-100 to-emerald-100 rounded-full flex items-center justify-center shadow-lg">
                  <i class="fas fa-check-circle text-5xl md:text-6xl text-success-500"></i>
                </div>
                <h3 class="text-3xl font-bold text-gray-900 mb-8">Upload Process Complete</h3>
            </div>
            <div class="flex justify-center">
                <button onclick="finalizeCreateLesson('${fileName.replace(/'/g, "\\'")}', ${fileSize}, '${lessonTitle.replace(/'/g, "\\'")}', '${lessonContext.replace(/'/g, "\\'")}', '${lessonSubject.replace(/'/g, "\\'")}', '${lessonGrade.replace(/'/g, "\\'")}')" 
                        class="bg-primary-600 text-white py-4 px-8 rounded-xl hover:bg-primary-700 transition-all font-bold text-lg shadow-lg hover:shadow-xl flex items-center justify-center gap-2">
                    Continue to Chat
                </button>
            </div>
        </div>
      `;
      }

      // Generate markdown lesson content from PDF metadata
      function generateMarkdownLessonFromPDF(lessonTitle, fileName, lessonContext, lessonSubject, lessonGrade) {
        // Parse book title to extract key topics
        const bookTitle = fileName.replace('.pdf', '').replace(/[_-]/g, ' ');

        const plainContent = `${lessonTitle.toUpperCase()}
==============================================================================

COURSE INFORMATION
──────────────────────────────────────────────────────────────────────────────
Subject: ${lessonSubject}
Grade Level: ${lessonGrade}
Source Material: ${bookTitle}
${lessonContext ? `Context: ${lessonContext}` : ''}


LEARNING OBJECTIVES
──────────────────────────────────────────────────────────────────────────────
By the end of this lesson, students will be able to:

1. Understand the foundational concepts of ${lessonTitle.toLowerCase()}
2. Apply key principles and theories to real-world scenarios
3. Analyze complex problems using the frameworks presented
4. Evaluate information critically and synthesize new knowledge
5. Create solutions and implementations based on learned concepts


INTRODUCTION
──────────────────────────────────────────────────────────────────────────────
This lesson is derived from "${bookTitle}" and provides a comprehensive introduction
to ${lessonTitle.toLowerCase()}. The content has been curated to match the ${lessonGrade}
grade level with appropriate complexity and real-world applications.


KEY CONCEPTS
──────────────────────────────────────────────────────────────────────────────

CONCEPT 1: FOUNDATIONAL PRINCIPLES
Definition: The core ideas that form the basis of ${lessonTitle.toLowerCase()}
Importance: Understanding these principles is crucial for mastery
Real-world Application: These concepts appear frequently in professional and academic settings
Example: When we encounter a problem in this domain, we should first identify which 
         foundational principle applies

CONCEPT 2: CORE THEORY & FRAMEWORK
Overview: A systematic way of thinking about ${lessonTitle.toLowerCase()}
Components: The major parts that make up this system
   • Primary element and its characteristics
   • Secondary element and relationships
   • Integration and synthesis
Why It Matters: This framework helps organize complex information

CONCEPT 3: PRACTICAL APPLICATION
Use Cases: Where and how this knowledge is applied
Best Practices: Do's and don'ts when applying concepts
Common Mistakes: What to avoid
Success Patterns: What typically works well


ESSENTIAL TOPICS
──────────────────────────────────────────────────────────────────────────────

TOPIC 1: INTRODUCTION & FUNDAMENTALS
• Background and history
• Why this matters today
• Common misconceptions

TOPIC 2: CORE METHODOLOGIES
• Step-by-step processes
• Tools and resources
• Workflow and best practices

TOPIC 3: ADVANCED CONCEPTS
• Building on fundamentals
• Complex scenarios
• Edge cases and exceptions

TOPIC 4: REAL-WORLD APPLICATIONS
• Case studies
• Industry examples
• Student projects and activities


CHAPTER SUMMARIES
──────────────────────────────────────────────────────────────────────────────

CHAPTER 1
Key points covered in the source material regarding ${lessonTitle.toLowerCase()}:
• Main concept and overview
• Historical context and evolution
• Current relevance and applications

CHAPTER 2
Development of ideas:
• Building upon chapter 1
• Introduction of complexity
• Practical implications

CHAPTER 3
Advanced topics:
• Synthesis of previous chapters
• Emerging trends and innovations
• Future directions


IMPORTANT DEFINITIONS
──────────────────────────────────────────────────────────────────────────────
Term 1: A key concept in this domain
Term 2: Another important framework or idea
Term 3: Essential vocabulary for this subject
Term 4: Specialized terminology unique to this field


DISCUSSION QUESTIONS
──────────────────────────────────────────────────────────────────────────────
1. How would you apply the concepts from this lesson to a real-world problem?
2. What assumptions underlie the frameworks presented?
3. How might these ideas evolve as technology and society change?
4. Can you find connections between this lesson and other subjects you've studied?
5. What are the limitations of the approaches discussed?


ASSESSMENT ACTIVITIES
──────────────────────────────────────────────────────────────────────────────

KNOWLEDGE CHECK
• Multiple choice questions on key concepts
• Short answer questions on definitions
• Fill-in-the-blank exercises

APPLICATION TASKS
• Solve practice problems using learned concepts
• Analyze provided case studies
• Design solutions to presented scenarios

CREATIVE PROJECTS
• Create a detailed presentation on a topic
• Develop a real-world application or tool
• Write a research paper on an advanced topic


KEY TAKEAWAYS
──────────────────────────────────────────────────────────────────────────────
1. CONCEPT: The fundamental idea that anchors this lesson
2. PRINCIPLE: The rule or guideline to follow
3. PRACTICE: How to apply these ideas effectively
4. PERSPECTIVE: Understanding the broader context and implications


RESOURCES FOR FURTHER LEARNING
──────────────────────────────────────────────────────────────────────────────
• Recommended readings from the source material
• Online resources for deeper understanding
• Practice problems and solutions
• Video tutorials and demonstrations
• Relevant articles and papers


CONCLUSION
──────────────────────────────────────────────────────────────────────────────
${lessonTitle} is a vital area of knowledge in ${lessonSubject}. By mastering the concepts,
frameworks, and applications covered in this lesson, you will develop a strong foundation
for advanced study and practical application. Remember that learning is iterative—revisit
these concepts regularly and apply them to new situations.

==============================================================================
Generated from: ${bookTitle}
Grade Level: ${lessonGrade}
Subject Area: ${lessonSubject}
Last Updated: ${new Date().toLocaleDateString()}`;

        return plainContent;
      }

      // Build the PDF upload success bubble (summary + action options).
      function buildPDFUploadSuccessMessage(opts) {
        opts = opts || {};
        const fileName = opts.fileName || opts.filename || 'document';
        const lessonTitle = opts.lessonTitle || opts.title || fileName.replace(/\.pdf$/i, '');
        const lessonSubject = opts.lessonSubject || opts.subject || '';
        const lessonGrade = opts.lessonGrade || opts.grade || '';
        const lessonContext = opts.lessonContext || opts.context || '';
        const fileSize = opts.fileSize || opts.size || null;
        const fileSizeMB = fileSize != null ? (Number(fileSize) / (1024 * 1024)).toFixed(2) : null;
        let pages = opts.pages || opts.numPages || opts.num_pages || null;
        if (pages == null && fileSize != null) {
          pages = Math.ceil(Number(fileSize) / 50000);
        }

        let details = '<strong>PDF Details:</strong><br>';
        details += '• <strong>PDF Name:</strong> ' + String(fileName).replace(/[<>]/g, '') + '<br>';
        if (lessonTitle) details += '• <strong>Title:</strong> ' + String(lessonTitle).replace(/[<>]/g, '') + '<br>';
        if (lessonSubject) details += '• <strong>Subject:</strong> ' + String(lessonSubject).replace(/[<>]/g, '') + '<br>';
        if (lessonGrade) details += '• <strong>Grade:</strong> ' + String(lessonGrade).replace(/[<>]/g, '') + '<br>';
        if (fileSizeMB != null) details += '• <strong>File Size:</strong> ' + fileSizeMB + ' MB<br>';
        if (pages != null) details += '• <strong>Pages:</strong> ' + pages + '<br>';
        if (lessonContext) details += '• <strong>Context:</strong> ' + String(lessonContext).replace(/[<>]/g, '') + '<br>';

        return '✅ <strong>Successfully uploaded "' + String(fileName).replace(/[<>"]/g, '') + '"!</strong><br><br>' +
          details + '<br>' +
          '<strong>📝 What would you like to do?</strong><br>' +
          'Type one of these commands:<br>' +
          '• "create a lesson" - Generate a lesson from this PDF<br>' +
          '• "summarize" - Get a brief summary<br>' +
          '• "ask me questions" - Generate quiz questions<br>' +
          '• Or ask your own questions about the content!';
      }

      // Finalization function - NO AUTO-SAVE
      async function finalizeCreateLesson(fileName, fileSize, lessonTitle, lessonContext, lessonSubject, lessonGrade, lessonMode) {
        closeCreateLessonModal();
        if (typeof window.tdResetCreateLessonForm === 'function') window.tdResetCreateLessonForm();

        const numPages = (window._createLessonMeta && window._createLessonMeta.numPages != null)
          ? window._createLessonMeta.numPages
          : Math.ceil(fileSize / 50000);

        // Store PDF metadata only. Do NOT set currentLessonMarkdown to the template here –
        // the actual lesson comes from the AI when the user says "create a lesson" in chat and is stored by RAG.
        currentPDFData = {
          id: 'lesson_' + Date.now(),
          title: lessonTitle,
          fileName: fileName,
          fileSize: fileSize,
          context: lessonContext,
          subject: lessonSubject,
          grade: lessonGrade,
          numPages: numPages,
          createdBy: "Good",
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          status: 'published',
          source: 'pdf_upload',
          version: 1
        };

        currentLessonMarkdown = null;

        // Save to localStorage so it persists across tab switches
        savePDFDataToStorage();

        var metaThreadId = (window._createLessonMeta && window._createLessonMeta.threadId) || window.currentRAGThreadId || null;
        var metaConvId = (window._createLessonMeta && window._createLessonMeta.conversationId) || null;

        if (lessonMode === 'as_is') {
          try {
            const res = await fetch('/api/lessons/create_from_uploaded_document', {
              method: 'POST',
              credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                title: lessonTitle,
                summary: lessonContext || ('Uploaded PDF lesson: ' + lessonTitle),
                focus_area: lessonSubject,
                grade_level: lessonGrade,
                filename: fileName,
                rag_thread_id: metaThreadId,
                conversation_id: metaConvId
              })
            });
            const body = await res.json().catch(function () { return {}; });
            if (!res.ok || !body.success) {
              throw new Error((body && body.error) || 'Could not save uploaded document as lesson');
            }
            const createdId = (body.lesson && body.lesson.id) || body.id;
            showToast('PDF saved as a draft lesson. Assign a class to publish.', 'success', 4000);
            await showMyLessonsPage();
            if (createdId && typeof openLessonAssignPublish === 'function') {
              setTimeout(function () {
                openLessonAssignPublish(createdId, lessonTitle);
              }, 300);
            }
            return;
          } catch (err) {
            console.error('Direct PDF lesson save failed', err);
            showToast(err.message || 'Could not save uploaded document as lesson.', 'error', 5000);
            return;
          }
        }

        showToast(`✅ PDF "${lessonTitle}" uploaded successfully!`, 'success', 4000);

        // Enable chat tab and switch to the NEW backend conversation (do not start a client-side chat)
        enableChatTab();
        showChatTab();

        // Prefer the conversation/thread created for THIS upload, fall back to global if needed
        var newConvId = metaConvId != null ? metaConvId : window.currentRAGConversationId;
        var successHtml = buildPDFUploadSuccessMessage({
          fileName: fileName,
          lessonTitle: lessonTitle,
          lessonSubject: lessonSubject,
          lessonGrade: lessonGrade,
          lessonContext: lessonContext,
          fileSize: fileSize,
          pages: numPages
        });

        if (newConvId != null) {
          setBackendConversationTitle(newConvId, lessonTitle);
          loadChatHistoryFromBackend().then(function () {
            return loadChatPromise(newConvId);
          }).then(function () {
            // Preamble already shows PDF summary + action bullets from currentPDFData.
            // Add a single user marker; avoid duplicating the assistant success bubble.
            addUserMessage('📚 Uploaded PDF: ' + lessonTitle);
          }).catch(function (e) { console.error('Error switching to new conversation', e); });
        } else {
          startNewChat();
          addUserMessage('📚 Uploaded PDF: ' + lessonTitle);
          setTimeout(function () {
            addAssistantMessage(successHtml);
          }, 300);
        }
      }

      // Show My Lessons Page (API-integrated: fetches from /api/lessons/my_lessons)
      // New teacher shell: renders into the Lessons view (#tdLessonsList); the chat DOM is left intact.
      async function showMyLessonsPage() {
        stopTextToSpeechSession();
        if (window.tdShowView) tdShowView('lessons');

        const listEl = document.getElementById('tdLessonsList');
        if (!listEl) return;
        listEl.innerHTML = '<div class="td-empty"><div class="td-spinner"></div><p>Loading lessons...</p></div>';

        let lessons = [];
        try {
          const params = new URLSearchParams({
            page: String(currentPage),
            per_page: String(lessonsPerPage)
          });
          if (teacherLessonSearchTerm) params.set('q', teacherLessonSearchTerm);
          const response = await fetch('/api/lessons/my_lessons?' + params.toString(), { method: 'GET', credentials: 'include' });
          if (response.ok) {
            const data = await response.json();
            lessons = data.lessons || [];
            teacherLessonsTotalPages = Math.max(1, Number(data.total_pages || 1));
            teacherLessonsTotalCount = Number(data.total || lessons.length || 0);
            currentPage = Math.max(1, Number(data.page || currentPage));
          }
        } catch (e) {
          console.error('Failed to load lessons', e);
          showToast('Failed to load lessons', 'error');
        }

        // Keep current page lessons only (server-side pagination)
        window.availableLessons = lessons;
        tdRenderLessonsList();
      }

      // Client-side view state for the lessons list (applies to the current server page only).
      window.tdLessonFilter = window.tdLessonFilter || { tab: 'all', subject: '', grade: '', sort: 'latest' };

      function tdLessonTimestamp(lesson) {
        const date = new Date(lesson.created_at || lesson.updated_at || 0);
        const diffDays = Math.floor(Math.abs(new Date() - date) / (1000 * 60 * 60 * 24));
        if (diffDays === 0) return 'Today ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        if (diffDays === 1) return 'Yesterday ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        if (diffDays < 7) return diffDays + ' days ago';
        return date.toLocaleDateString();
      }

      function tdRenderLessonsList() {
        const listEl = document.getElementById('tdLessonsList');
        if (!listEl) return;
        const lessons = window.availableLessons || [];
        const f = window.tdLessonFilter;

        // Populate subject / grade filters from the loaded lessons (backend has no filter params).
        const subjSel = document.getElementById('tdLessonSubjectFilter');
        const gradeSel = document.getElementById('tdLessonGradeFilter');
        const uniq = (arr) => Array.from(new Set(arr.filter(Boolean).map(String))).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
        if (subjSel) {
          const subjects = uniq(lessons.map(l => l.focus_area || l.focusArea || 'General'));
          subjSel.innerHTML = '<option value="">All subjects</option>' + subjects.map(s => '<option value="' + escapeHtml(s) + '"' + (s === f.subject ? ' selected' : '') + '>' + escapeHtml(s) + '</option>').join('');
        }
        if (gradeSel) {
          const grades = uniq(lessons.map(l => l.grade_level || l.gradeLevel || 'General'));
          gradeSel.innerHTML = '<option value="">All grades</option>' + grades.map(g => '<option value="' + escapeHtml(g) + '"' + (g === f.grade ? ' selected' : '') + '>' + escapeHtml(g) + '</option>').join('');
        }

        const pubCount = lessons.filter(l => l.is_public === true).length;
        const showCounts = teacherLessonsTotalPages <= 1;
        const setTab = (id, label, count) => {
          const el = document.getElementById(id);
          if (el) el.textContent = showCounts ? label + ' (' + count + ')' : label;
        };
        const allTab = document.getElementById('tdLessonTabAll');
        if (allTab) allTab.textContent = 'All lessons (' + teacherLessonsTotalCount + ')';
        // Published / Drafts counts are exact only when every lesson is on this page.
        setTab('tdLessonTabPublished', 'Published', pubCount);
        setTab('tdLessonTabDrafts', 'Drafts', lessons.length - pubCount);
        document.querySelectorAll('#view-lessons .td-subtabs [data-lesson-tab]').forEach(el => {
          el.classList.toggle('active', el.getAttribute('data-lesson-tab') === f.tab);
        });

        let rows = lessons.filter(l => {
          if (f.tab === 'published' && l.is_public !== true) return false;
          if (f.tab === 'drafts' && l.is_public === true) return false;
          if (f.subject && String(l.focus_area || l.focusArea || 'General') !== f.subject) return false;
          if (f.grade && String(l.grade_level || l.gradeLevel || 'General') !== f.grade) return false;
          return true;
        });
        const ts = (l) => new Date(l.created_at || l.updated_at || 0).getTime();
        if (f.sort === 'oldest') rows.sort((a, b) => ts(a) - ts(b));
        else if (f.sort === 'title') rows.sort((a, b) => String(a.title || '').localeCompare(String(b.title || '')));
        else rows.sort((a, b) => ts(b) - ts(a));

        if (lessons.length === 0) {
          listEl.innerHTML = `
            <div class="td-empty">
              <img src="${window.TEACHER_CFG.icons.lessons}" alt="" class="td-empty-ic">
              <h3>No lessons yet</h3>
              <p>You haven't created any lessons yet. Click <strong>Create Lesson</strong> above to get started.</p>
            </div>`;
          updatePagination();
          return;
        }
        if (rows.length === 0) {
          listEl.innerHTML = '<div class="td-empty"><h3>No matching lessons</h3><p>Try a different tab or filter.</p></div>';
          updatePagination();
          return;
        }

        const offset = (currentPage - 1) * lessonsPerPage;
        listEl.innerHTML = rows.map((lesson, idx) => {
          const id = escapeHtml(String(lesson.id));
          const title = escapeHtml(lesson.title || 'Untitled Lesson');
          const subject = escapeHtml(lesson.focus_area || lesson.focusArea || 'General');
          const grade = escapeHtml(String(lesson.grade_level || lesson.gradeLevel || 'General'));
          const createdBy = escapeHtml(lesson.teacher_name || (window.teacherUserInfo && window.teacherUserInfo.username) || 'Teacher');
          const isPublished = lesson.is_public === true;
          const version = lesson.version_number || lesson.version || 1;
          const no = String(offset + idx + 1).padStart(2, '0');
          return `
          <div class="td-lesson-row" data-lesson-id="${id}">
            <div class="td-lesson-bar"><span>LESSON ${no}</span></div>
            <div class="td-row-body">
              <div class="td-row-top">
                <div class="td-title-chip">
                  <div class="td-avatar-sm">${escapeHtml((lesson.title || '?').charAt(0).toUpperCase())}</div>
                  <h3>${title}</h3>
                </div>
                <div class="td-side-meta">
                  <div class="version-dropdown-wrapper"><button class="version-dropdown-btn td-version-chip" onclick="toggleVersionDropdown(event, '${id}', this)">v${version}<i class="fas fa-chevron-down version-chevron"></i></button><div class="version-dropdown-menu" id="vdrop-${id}"></div></div>
                  <span class="td-time-ago">${escapeHtml(tdLessonTimestamp(lesson))}</span>
                </div>
              </div>
              <div class="td-meta-line">
                <div><span class="td-label">SUBJECT</span><b>${subject}</b></div>
                <div><span class="td-label">GRADE</span><b>${grade}</b></div>
                <div><span class="td-label">CREATED BY</span><b>${createdBy}</b></div>
                <div><span class="td-label">VISIBILITY</span><b>${isPublished ? 'Published to class' : 'Draft — assign a class to publish'}</b></div>
              </div>
              <div class="td-actions-line">
                <a title="View" onclick="viewLesson('${id}')"><i class="fas fa-eye"></i> View</a>
                <a title="Edit" onclick="editLesson('${id}')"><i class="fas fa-pen"></i> Edit</a>
                <a title="Download DOCX" onclick="downloadLessonDocx('${id}')"><i class="fas fa-file-word"></i> Word</a>
                <a title="Download PPT" onclick="downloadLessonPPT('${id}')"><i class="fas fa-file-powerpoint"></i> PowerPoint</a>
                <a title="FAQ" onclick="showLessonFAQ('${id}')"><i class="fas fa-circle-question"></i> FAQ</a>
                <a title="Assign to class & publish" onclick="openLessonAssignPublish('${id}', ${JSON.stringify(lesson.title || 'Lesson').replace(/</g, '\\u003c')})"><i class="fas fa-users"></i> Assign &amp; Publish</a>
                ${isPublished
                  ? `<a title="Unpublish lesson" onclick="toggleLessonPublication('${id}', false)"><i class="fas fa-eye-slash"></i> Unpublish</a>`
                  : `<a title="Publish lesson to a class" onclick="openLessonAssignPublish('${id}', ${JSON.stringify(lesson.title || 'Lesson').replace(/</g, '\\u003c')})"><i class="fas fa-paper-plane"></i> Publish</a>`}
                ${lesson.has_child_version !== true ? `<a class="danger" title="Delete" onclick="deleteLesson('${id}')"><i class="fas fa-trash"></i> Delete</a>` : ''}
              </div>
            </div>
          </div>`;
        }).join('');

        updatePagination();
      }
      window.tdRenderLessonsList = tdRenderLessonsList;

      // Filter lessons based on search
      function filterLessons(searchTerm) {
        teacherLessonSearchTerm = (searchTerm || '').trim();
        currentPage = 1;
        showMyLessonsPage();
      }

      // Find lesson by ID
      function findLessonById(lessonId) {
        const lessons = window.availableLessons || JSON.parse(localStorage.getItem('teacher_lessons') || '[]');
        if (!lessonId) return -1;
        const needle = String(lessonId);
        return lessons.findIndex(lesson => String(lesson.id) === needle);
      }

      // Lesson actions - API: fetch /api/lessons/lesson/:id/view then show modal.
      // Pass selectedVersionNumber to open at a specific version (default = latest).
      async function viewLesson(lessonId, selectedVersionNumber) {
        if (!lessonId) { showToast('No lesson ID provided', 'error'); return; }
        try {
          const response = await fetch('/api/lessons/lesson/' + lessonId + '/view', { method: 'GET', credentials: 'include' });
          if (!response.ok) { showToast('Failed to load lesson', 'error'); return; }
          const data = await response.json();
          const lesson = data.lesson;
          const versions = data.versions || [];
          if (!lesson) { showToast('Lesson not found', 'error'); return; }
          const sortedVersions = versions.slice().sort((a, b) => (b.version_number || b.version || 0) - (a.version_number || a.version || 0));
          window.currentLessonVersions = sortedVersions;

          // Show the lesson the teacher clicked — not the newest sibling in a
          // version family. Same-chat photosynthesis vs water cycle are separate
          // lessons; opening one must not display the other's saved content.
          const latestVersion = sortedVersions[0] || lesson;
          const latestVersionNum = latestVersion.version_number || latestVersion.version || 1;
          let targetVersion = sortedVersions.find(function(v) {
            return String(v.id) === String(lessonId);
          }) || lesson;
          if (selectedVersionNumber != null) {
            const found = sortedVersions.find(function(v) {
              return (v.version_number || v.version || 1) === selectedVersionNumber;
            });
            if (found) targetVersion = found;
          }
          const viewedVersionNum = targetVersion.version_number || targetVersion.version || 1;
          const isLatestVersion = viewedVersionNum === latestVersionNum;

          const forModal = {
            id: lesson.id,
            title: targetVersion.title || lesson.title,
            subject: targetVersion.focus_area || lesson.focus_area || lesson.focusArea,
            grade: targetVersion.grade_level || lesson.grade_level || lesson.gradeLevel,
            content: targetVersion.content || lesson.content,
            created_at: targetVersion.created_at || lesson.created_at,
            createdAt: targetVersion.created_at || lesson.created_at,
            version: viewedVersionNum,
            versionLessonId: targetVersion.id || lesson.id,
            versions: sortedVersions.map(function(v) {
              return {
                id: v.id,
                version: v.version_number || v.version || 1,
                createdAt: v.created_at,
                content: v.content,
                title: v.title,
                summary: v.summary,
                source_pdf_url: v.source_pdf_url || null,
                source_view_mode: v.source_view_mode || null,
              };
            }),
            status: lesson.status || 'draft',
            isLatestVersion: isLatestVersion,
            latestVersionNum: latestVersionNum,
            // Fields required for conversation summary lookup
            rag_thread_id: lesson.rag_thread_id || null,
            conversation_id: lesson.conversation_id || null,
            summary: targetVersion.summary || lesson.summary || null,
            source_pdf_url: targetVersion.source_pdf_url || lesson.source_pdf_url || null,
            source_view_mode: targetVersion.source_view_mode || lesson.source_view_mode || null,
          };
          await populateViewLessonModal(forModal);
          showViewLessonModal();
        } catch (err) {
          console.error('viewLesson error:', err);
          showToast('Error loading lesson: ' + (err.message || 'Unknown'), 'error');
        }
      }

      function openLessonAssignPublish(lessonId, lessonTitle) {
        const preferred =
          (document.getElementById('lessonAssignClass') && document.getElementById('lessonAssignClass').value) ||
          window._pendingLessonAssignClassId ||
          '';
        if (typeof window.openLmsLessonAssignModal === 'function') {
          window.openLmsLessonAssignModal(lessonId, lessonTitle || 'Lesson', preferred || null);
          return;
        }
        showToast('Assign form is unavailable. Refresh the page and try again.', 'error', 4000);
      }
      window.openLessonAssignPublish = openLessonAssignPublish;

      async function toggleLessonPublication(lessonId, publish) {
        // Publishing always goes through class assignment (quiz-parity).
        if (publish) {
          const lessons = window.availableLessons || [];
          const match = lessons.find(function (l) { return String(l.id) === String(lessonId); });
          openLessonAssignPublish(lessonId, (match && match.title) || 'Lesson');
          return;
        }
        try {
          const response = await fetch('/api/lessons/lesson/' + encodeURIComponent(lessonId), {
            method: 'PUT',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_public: false })
          });
          const data = await response.json().catch(function () { return {}; });
          if (!response.ok || !data.success) {
            throw new Error(data.error || 'Could not unpublish lesson');
          }
          showToast('Lesson unpublished. Students can no longer access it.', 'success', 3500);
          await showMyLessonsPage();
        } catch (err) {
          console.error('Lesson publication update failed', err);
          showToast(err.message || 'Could not unpublish lesson', 'error', 4000);
        }
      }

      function setViewLessonSourcePdfMode(enabled) {
        const modal = document.getElementById('viewLessonModal');
        if (modal) modal.classList.toggle('vl-source-pdf-mode', !!enabled);
        const finalizeBtn = document.getElementById('finalizeVersionBtn');
        if (finalizeBtn && enabled) {
          finalizeBtn.classList.add('hidden');
          finalizeBtn.disabled = true;
        } else if (finalizeBtn) {
          finalizeBtn.disabled = false;
        }
      }

      function ensurePdfJsReady() {
        if (!window.pdfjsLib) return false;
        try {
          if (!pdfjsLib.GlobalWorkerOptions.workerSrc) {
            pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';
          }
        } catch (e) {
          console.warn('Failed to configure pdf.js worker', e);
        }
        return true;
      }

      async function renderSourcePdfLesson(container, pdfUrl) {
        if (!container) return;
        const lessonForChat = window.currentViewedLessonData || {};
        const chatButton = lessonForChat.rag_thread_id
          ? '<button type="button" class="source-pdf-chat-btn" onclick="openLessonSourceChat()"><i class="fas fa-comments"></i> Chat with PDF</button>'
          : '';
        container._pdfRenderGen = (container._pdfRenderGen || 0) + 1;
        const gen = container._pdfRenderGen;
        container.classList.remove('tex2jax_process');
        container.innerHTML =
          '<div class="source-pdf-viewer">' +
          '<div class="source-pdf-toolbar"><span><i class="fas fa-file-pdf"></i> Original PDF view</span>' +
          '<span style="display:flex;align-items:center;gap:10px;">' + chatButton +
          '<a href="' + escapeHtml(pdfUrl) + '" target="_blank" rel="noopener">Open PDF</a></span></div>' +
          '<div class="text-gray-500 py-8"><i class="fas fa-spinner fa-spin"></i> Loading PDF...</div>' +
          '</div>';

        if (!ensurePdfJsReady()) {
          container.innerHTML =
            '<div class="source-pdf-viewer"><p class="text-red-700">PDF viewer library did not load.</p>' +
            '<a href="' + escapeHtml(pdfUrl) + '" target="_blank" rel="noopener">Open PDF directly</a></div>';
          return;
        }

        try {
          const viewer = container.querySelector('.source-pdf-viewer');
          const loadingTask = pdfjsLib.getDocument({ url: pdfUrl, withCredentials: true });
          const pdf = await loadingTask.promise;
          if (container._pdfRenderGen !== gen) return;
          viewer.innerHTML =
            '<div class="source-pdf-toolbar"><span><i class="fas fa-file-pdf"></i> Original PDF view · ' +
            pdf.numPages + ' page' + (pdf.numPages === 1 ? '' : 's') + '</span>' +
            '<span style="display:flex;align-items:center;gap:10px;">' + chatButton +
            '<a href="' + escapeHtml(pdfUrl) + '" target="_blank" rel="noopener">Open PDF</a></span></div>';

          const availableWidth = Math.max(320, Math.min(viewer.clientWidth || 900, 980) - 42);
          for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
            if (container._pdfRenderGen !== gen) return;
            const page = await pdf.getPage(pageNum);
            const baseViewport = page.getViewport({ scale: 1 });
            const scale = Math.min(1.55, Math.max(0.65, availableWidth / baseViewport.width));
            const viewport = page.getViewport({ scale: scale });
            const pageWrap = document.createElement('div');
            pageWrap.className = 'source-pdf-page';
            pageWrap.style.width = viewport.width + 'px';
            pageWrap.style.height = viewport.height + 'px';
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            canvas.width = Math.floor(viewport.width);
            canvas.height = Math.floor(viewport.height);
            canvas.style.width = viewport.width + 'px';
            canvas.style.height = viewport.height + 'px';
            pageWrap.appendChild(canvas);
            const linkLayer = document.createElement('div');
            linkLayer.className = 'source-pdf-link-layer';
            pageWrap.appendChild(linkLayer);
            viewer.appendChild(pageWrap);

            await page.render({ canvasContext: ctx, viewport: viewport }).promise;
            const annotations = await page.getAnnotations({ intent: 'display' });
            annotations.forEach(function (annotation) {
              const href = annotation.url || annotation.unsafeUrl || '';
              if (!href || !annotation.rect) return;
              const rect = viewport.convertToViewportRectangle(annotation.rect);
              const left = Math.min(rect[0], rect[2]);
              const top = Math.min(rect[1], rect[3]);
              const width = Math.abs(rect[0] - rect[2]);
              const height = Math.abs(rect[1] - rect[3]);
              const a = document.createElement('a');
              a.href = href;
              a.target = '_blank';
              a.rel = 'noopener noreferrer';
              a.title = href;
              a.style.left = left + 'px';
              a.style.top = top + 'px';
              a.style.width = width + 'px';
              a.style.height = height + 'px';
              linkLayer.appendChild(a);
            });
          }
        } catch (err) {
          console.error('PDF render failed', err);
          container.innerHTML =
            '<div class="source-pdf-viewer"><p class="text-red-700">Could not render this PDF preview.</p>' +
            '<a href="' + escapeHtml(pdfUrl) + '" target="_blank" rel="noopener">Open PDF directly</a></div>';
        }
      }

      async function openLessonSourceChat() {
        const lesson = window.currentViewedLessonData || {};
        if (!lesson.rag_thread_id) {
          showToast('No PDF chat context is linked to this lesson.', 'warning');
          return;
        }
        window.currentRAGThreadId = lesson.rag_thread_id;
        try { localStorage.setItem('teacher_currentRAGThreadId', lesson.rag_thread_id); } catch (e) {}
        if (lesson.conversation_id) {
          window.currentRAGConversationId = lesson.conversation_id;
          try { localStorage.setItem('teacher_currentRAGConversationId', String(lesson.conversation_id)); } catch (e) {}
        }
        closeViewLessonModal();
        enableChatTab();
        showChatTab();
        try {
          if (lesson.conversation_id) {
            await loadChatPromise(lesson.conversation_id);
          }
        } catch (e) {
          console.warn('Could not load linked PDF conversation', e);
        }
        // This PDF is already a saved lesson. Keep its chat available for Q&A,
        // but do not offer Save again and create a duplicate lesson.
        window.currentLessonChatAlreadySaved = true;
        setSaveLessonButtonsVisible(false);
        showToast('PDF chat context opened. Ask your question in the chat box.', 'success', 3500);
      }

      // Render markdown into an element and apply post-processing (math/code formatting)
      function renderLessonMarkdown(el, markdownText, emptyHtml) {
        if (!el) return;
        const raw = (markdownText == null) ? '' : String(markdownText);
        el._lessonRenderGen = (el._lessonRenderGen || 0) + 1;
        const renderGen = el._lessonRenderGen;
        if (!raw.trim()) {
          el.innerHTML = emptyHtml || '';
          return;
        }

        if (el.tagName === 'TEXTAREA') {
          el.value = raw;
          return;
        }

        el.classList.add('tex2jax_process');
        if (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.formatChatResponse) {
          el.innerHTML = TeacherChatFormatter.formatChatResponse(raw);
        } else if (typeof marked !== 'undefined' && marked.parse) {
          el.innerHTML = marked.parse(raw);
        } else {
          el.textContent = raw;
        }

        if (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.processRenderedContent) {
          TeacherChatFormatter.processRenderedContent(el).then(function () {
            if (el._lessonRenderGen !== renderGen) return;
            syncExpandedLessonPreviewFromCurrent();
          });
        } else if (typeof window.typesetMathIn === 'function') {
          window.typesetMathIn(el).then(function () {
            if (el._lessonRenderGen !== renderGen) return;
            syncExpandedLessonPreviewFromCurrent();
          }).catch(function () {});
        }
      }

      function syncExpandedLessonPreviewFromCurrent() {
        const sourceEl = document.getElementById('viewLessonCurrent');
        const expandedEl = document.getElementById('expandedLessonPreviewContent');
        if (!sourceEl || !expandedEl) return;
        const lesson = window.currentViewedLessonData || {};
        if (lesson.source_pdf_url) {
          renderSourcePdfLesson(expandedEl, lesson.source_pdf_url);
          return;
        }
        expandedEl.innerHTML = sourceEl.innerHTML || '<p class="text-gray-500 italic">No content available.</p>';
      }

      function openExpandedLessonPreview() {
        const modal = document.getElementById('expandedLessonPreviewModal');
        if (!modal) return;

        // Keep overlay out of any transformed/stacked parent contexts.
        try {
          if (modal.parentElement !== document.body) {
            document.body.appendChild(modal);
          }
        } catch (e) {
          console.warn('Unable to move expanded modal to body:', e);
        }

        const lessonTitle = (document.getElementById('viewLessonTitle')?.textContent || 'Lesson').trim();
        const versionLabel = (document.getElementById('viewLessonVersion')?.textContent || '').trim();
        const subtitleEl = document.getElementById('expandedLessonPreviewSubtitle');
        if (subtitleEl) {
          subtitleEl.textContent = versionLabel ? `${lessonTitle} • ${versionLabel}` : lessonTitle;
        }

        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        modal.style.visibility = 'visible';
        modal.style.opacity = '1';
        modal.style.pointerEvents = 'auto';
        document.body.style.overflow = 'hidden';
        syncExpandedLessonPreviewFromCurrent();
      }

      function syncExpandedConversationSummaryFromCurrent() {
        const sourceEl = document.getElementById('viewLessonConversationSummaryContent');
        const expandedEl = document.getElementById('expandedConversationSummaryContent');
        if (!sourceEl || !expandedEl) return;
        expandedEl.innerHTML = sourceEl.innerHTML || '<p class="text-gray-500 italic">No summary available yet.</p>';
      }

      function openExpandedConversationSummary() {
        const modal = document.getElementById('expandedConversationSummaryModal');
        if (!modal) return;
        try {
          if (modal.parentElement !== document.body) {
            document.body.appendChild(modal);
          }
        } catch (e) {
          console.warn('Unable to move expanded summary modal to body:', e);
        }
        syncExpandedConversationSummaryFromCurrent();
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        modal.style.visibility = 'visible';
        modal.style.opacity = '1';
        modal.style.pointerEvents = 'auto';
        document.body.style.overflow = 'hidden';
      }

      function closeExpandedConversationSummary() {
        const modal = document.getElementById('expandedConversationSummaryModal');
        if (!modal) return;
        modal.classList.add('hidden');
        modal.style.display = '';
        modal.style.visibility = '';
        modal.style.opacity = '';
        modal.style.pointerEvents = '';
        const viewLessonModal = document.getElementById('viewLessonModal');
        const expandedLessonModal = document.getElementById('expandedLessonPreviewModal');
        if (
          (!viewLessonModal || viewLessonModal.classList.contains('hidden')) &&
          (!expandedLessonModal || expandedLessonModal.classList.contains('hidden'))
        ) {
          document.body.style.overflow = '';
        }
      }

      function closeExpandedLessonPreview() {
        const modal = document.getElementById('expandedLessonPreviewModal');
        if (!modal) return;
        modal.classList.add('hidden');
        modal.style.display = '';
        modal.style.visibility = '';
        modal.style.opacity = '';
        modal.style.pointerEvents = '';
        const viewLessonModal = document.getElementById('viewLessonModal');
        if (!viewLessonModal || viewLessonModal.classList.contains('hidden')) {
          document.body.style.overflow = '';
        }
      }

      function toggleMainLessonModalExpand() {
        const panel = document.querySelector('#viewLessonModal .view-lesson-modal-panel');
        const icon = document.getElementById('expandMainLessonModalIcon');
        const btn = document.getElementById('expandMainLessonModalBtn');
        if (!panel) return;

        const isMaximized = panel.classList.toggle('is-maximized');
        if (icon) {
          icon.classList.toggle('fa-expand', !isMaximized);
          icon.classList.toggle('fa-compress', isMaximized);
        }
        if (btn) {
          btn.title = isMaximized ? 'Restore window size' : 'Expand window';
          btn.setAttribute('aria-label', btn.title);
        }
      }

      // Toggle the Conversation Summary / Lesson Summary detail panels under the
      // View Lesson timeline row (new_ui/teacher_dashbaordui/6-view-lesson.html tl-item design).
      function toggleViewLessonTlPanel(which) {
        const panels = {
          conv: document.getElementById('viewLessonConvPanel'),
          lesson: document.getElementById('viewLessonLessonPanel'),
        };
        const target = panels[which];
        if (!target) return;
        const isOpening = target.classList.contains('hidden');

        Object.values(panels).forEach(function (panel) {
          if (panel) panel.classList.add('hidden');
        });
        document.querySelectorAll('#viewLessonSummaryTimeline .vl-tl-item').forEach(function (item) {
          item.classList.remove('open');
        });

        if (isOpening) {
          target.classList.remove('hidden');
          const items = document.querySelectorAll('#viewLessonSummaryTimeline .vl-tl-item');
          const idx = which === 'conv' ? 0 : 1;
          if (items[idx]) items[idx].classList.add('open');
          if (which === 'lesson') {
            loadLessonSummaryForLesson(window.currentViewedLessonData);
          }
        }
      }

      function setFinalizeButtonVisible(isVisible) {
        const finalizeBtn = document.getElementById('finalizeVersionBtn');
        if (!finalizeBtn) return;
        // Never show finalize if the user is viewing an older version
        if (isVisible && window.currentViewingVersionIsLatest === false) return;
        if (isVisible) {
          finalizeBtn.classList.remove('hidden');
        } else {
          finalizeBtn.classList.add('hidden');
        }
      }

      function setViewLessonDraftMarkdown(markdown, emptyHtml) {
        const raw = markdown == null ? '' : String(markdown);
        window._viewLessonDraftMarkdown = raw;
        window._viewLessonDraftDirty = false;
        const draftEl = document.getElementById('viewLessonDraft');
        if (!draftEl) return;
        draftEl.setAttribute('data-raw-markdown', raw);
        renderLessonMarkdown(
          draftEl,
          raw,
          emptyHtml || '<span class="text-gray-400 italic">Draft will appear here after applying AI or making manual edits...</span>'
        );
      }

      function getViewLessonDraftMarkdown() {
        const draftEl = document.getElementById('viewLessonDraft');
        const stored = (
          window._viewLessonDraftMarkdown != null
            ? String(window._viewLessonDraftMarkdown)
            : (draftEl && draftEl.getAttribute('data-raw-markdown')) || ''
        ).trim();
        // Prefer stored markdown (AI enhance / loaded draft) so finalize does not
        // strip headings via contenteditable innerText.
        if (stored && !window._viewLessonDraftDirty) {
          return stored;
        }
        return draftEl ? (draftEl.innerText || '').trim() : '';
      }

      function clearViewLessonDraftEditor() {
        const draftEl = document.getElementById('viewLessonDraft');
        if (draftEl) {
          setViewLessonDraftMarkdown('');
        }
        window._viewLessonDraftMarkdown = '';
        window._viewLessonDraftDirty = false;
        const promptEl = document.getElementById('viewLessonPrompt');
        if (promptEl) promptEl.value = '';
        setFinalizeButtonVisible(false);
      }

      function setOlderVersionNotice(isOlder, viewedVersion, latestVersionNum) {
        const slot = document.getElementById('olderVersionNoticeSlot');
        const existingNotice = document.getElementById('olderVersionNotice');
        if (existingNotice) existingNotice.remove();
        if (!slot) return;
        if (!isOlder) {
          slot.innerHTML = '';
          slot.classList.add('hidden');
          return;
        }
        const notice = document.createElement('div');
        notice.id = 'olderVersionNotice';
        notice.className = 'version-older-notice';
        notice.innerHTML = '<i class="fas fa-info-circle"></i>'
          + ' Viewing v' + viewedVersion + ' (older). New versions can only be created from the latest (v'
          + latestVersionNum + ').';
        slot.appendChild(notice);
        slot.classList.remove('hidden');
      }

      function extractConversationIdFromLesson(lesson) {
        if (!lesson) return null;
        if (lesson.conversation_id) return Number(lesson.conversation_id) || null;
        const threadId = String(lesson.rag_thread_id || lesson.thread_id || '');
        const match = threadId.match(/_conv_(\d+)/);
        return match ? Number(match[1]) : null;
      }

      function setConversationSummaryLoading(isLoading) {
        const spinner = document.getElementById('viewLessonConversationSummarySpinner');
        const refreshBtn = document.getElementById('refreshConversationSummaryBtn');
        if (spinner) spinner.classList.toggle('hidden', !isLoading);
        if (refreshBtn) refreshBtn.classList.toggle('hidden', isLoading);
      }

      function renderConversationSummaryText(text, state) {
        const target = document.getElementById('viewLessonConversationSummaryContent');
        if (!target) return;
        if (state === 'error') {
          target.innerHTML = '<p class="text-red-600">Failed to load summary</p>';
          syncExpandedConversationSummaryFromCurrent();
          return;
        }
        if (!text) {
          target.innerHTML = '<p class="text-gray-500 italic">No summary available yet</p>';
          syncExpandedConversationSummaryFromCurrent();
          return;
        }
        try {
          if (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.formatChatResponse) {
            target.innerHTML = TeacherChatFormatter.formatChatResponse(String(text));
            syncExpandedConversationSummaryFromCurrent();
            return;
          }
        } catch (e) { /* fall through to pre-wrap fallback */ }
        // Fallback: preserve newlines and bullets using pre-wrap
        const escaped = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        target.innerHTML = '<pre class="whitespace-pre-wrap font-sans text-xs md:text-sm text-gray-700 leading-relaxed m-0 p-0">' + escaped + '</pre>';
        syncExpandedConversationSummaryFromCurrent();
      }

      function isPlaceholderLessonSummary(text) {
        const summary = String(text || '').trim().toLowerCase();
        return !summary || summary === 'saved from chat.' || summary === 'saved from chat';
      }

      function setLessonSummaryLoading(isLoading) {
        const spinner = document.getElementById('viewLessonLessonSummarySpinner');
        if (spinner) spinner.classList.toggle('hidden', !isLoading);
      }

      function updateLessonSummaryTimelineSub(text) {
        const sub = document.getElementById('viewLessonSummaryTlSub');
        if (!sub) return;
        const value = String(text || '').trim();
        if (!value || isPlaceholderLessonSummary(value)) {
          sub.textContent = 'Click to view';
          return;
        }
        sub.textContent = value.length > 72 ? (value.slice(0, 72).trim() + '…') : value;
      }

      function renderLessonSummaryText(lesson, state) {
        const target = document.getElementById('viewLessonStructuredSummary');
        if (!target) return;
        if (state === 'loading') {
          target.innerHTML = '<p class="text-gray-500 italic">Generating lesson summary...</p>';
          updateLessonSummaryTimelineSub('Generating...');
          return;
        }
        if (state === 'error') {
          target.innerHTML = '<p class="text-red-600">Failed to load lesson summary</p>';
          updateLessonSummaryTimelineSub('Unavailable');
          return;
        }
        const summary = (lesson && lesson.summary) ? String(lesson.summary).trim() : '';
        if (isPlaceholderLessonSummary(summary)) {
          target.innerHTML = '<p class="text-gray-500 italic">No lesson summary available</p>';
          updateLessonSummaryTimelineSub('Click to view');
          return;
        }
        updateLessonSummaryTimelineSub(summary);
        try {
          if (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.formatChatResponse) {
            target.innerHTML = TeacherChatFormatter.formatChatResponse(summary);
            return;
          }
        } catch (e) { /* fall through to pre-wrap fallback */ }
        const escaped = summary.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        target.innerHTML = '<pre class="whitespace-pre-wrap font-sans text-xs md:text-sm text-gray-700 leading-relaxed m-0 p-0">' + escaped + '</pre>';
      }

      async function loadLessonSummaryForLesson(lesson) {
        const token = ++lessonSummaryRequestToken;
        if (!lesson) {
          renderLessonSummaryText(null);
          return;
        }
        if (!isPlaceholderLessonSummary(lesson.summary)) {
          renderLessonSummaryText(lesson);
          return;
        }

        const summaryLessonId = Number(lesson.versionLessonId || lesson.id) || null;
        if (!summaryLessonId) {
          renderLessonSummaryText(null);
          return;
        }

        try {
          setLessonSummaryLoading(true);
          renderLessonSummaryText(lesson, 'loading');
          const summaryResponse = await fetch('/api/lessons/lesson/' + summaryLessonId + '/summary', {
            method: 'GET',
            credentials: 'include',
          });
          const summaryData = await summaryResponse.json().catch(function () { return {}; });
          if (token !== lessonSummaryRequestToken) return;
          if (!summaryResponse.ok) throw new Error(summaryData.error || 'Failed to load lesson summary');

          const generated = String(summaryData.summary || '').trim();
          if (isPlaceholderLessonSummary(generated)) {
            renderLessonSummaryText(null);
            return;
          }
          lesson.summary = generated;
          if (window.currentViewedLessonData) {
            window.currentViewedLessonData.summary = generated;
          }
          renderLessonSummaryText(lesson);
        } catch (error) {
          if (token !== lessonSummaryRequestToken) return;
          console.warn('Lesson summary load failed:', error);
          renderLessonSummaryText(null, 'error');
        } finally {
          if (token === lessonSummaryRequestToken) {
            setLessonSummaryLoading(false);
          }
        }
      }

      async function loadConversationSummaryForLesson(lesson, forceRefresh) {
        const token = ++conversationSummaryRequestToken;
        const conversationId = extractConversationIdFromLesson(lesson);
        const lessonId = lesson && lesson.id ? Number(lesson.id) : null;
        if (!conversationId) {
          renderConversationSummaryText(null);
          return;
        }

        try {
          setConversationSummaryLoading(false);

          // When forceRefresh is requested, skip the GET check and go straight to regenerate
          if (!forceRefresh) {
            const summaryResponse = await fetch(
              `/api/conversations/${conversationId}/summary${lessonId ? `?lesson_id=${lessonId}` : ''}`,
              { method: 'GET', credentials: 'include' }
            );
            const summaryData = await summaryResponse.json().catch(function () { return {}; });
            if (token !== conversationSummaryRequestToken) return;
            if (!summaryResponse.ok) throw new Error(summaryData.error || 'Failed to load summary');

            const currentSummaryText = summaryData.summary || null;
            if (!summaryData.is_outdated && currentSummaryText) {
              renderConversationSummaryText(currentSummaryText);
              return;
            }
            // Show stale summary (if any) while regenerating
            renderConversationSummaryText(currentSummaryText);
          }

          setConversationSummaryLoading(true);
          const regenerateResponse = await fetch(`/api/conversations/${conversationId}/summary/regenerate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ lesson_id: lessonId, force: true }),
          });
          const regenerateData = await regenerateResponse.json().catch(function () { return {}; });
          if (token !== conversationSummaryRequestToken) return;
          if (!regenerateResponse.ok) throw new Error(regenerateData.error || 'Failed to regenerate summary');
          renderConversationSummaryText(regenerateData.summary || null);
        } catch (error) {
          if (token !== conversationSummaryRequestToken) return;
          console.warn('Conversation summary load failed:', error);
          renderConversationSummaryText(null, 'error');
        } finally {
          if (token === conversationSummaryRequestToken) {
            setConversationSummaryLoading(false);
          }
        }
      }

      // Populate the view lesson modal with data
      async function populateViewLessonModal(lesson) {
        try {
          console.log('📝 populateViewLessonModal called with lesson:', lesson);

          // Store current lesson ID globally for button handlers
          window.currentViewLessonId = lesson.id;
          window.currentViewedLessonData = lesson;

          // Populate metadata
          const titleEl = document.getElementById('viewLessonTitle');
          if (titleEl) titleEl.textContent = lesson.title || 'Untitled Lesson';

          const subtitleEl = document.getElementById('viewLessonSubtitle');
          if (subtitleEl) subtitleEl.textContent = `${lesson.subject || 'General'} • Grade ${lesson.grade || 'N/A'}`;

          const statusEl = document.getElementById('viewLessonStatus');
          if (statusEl) statusEl.textContent = lesson.status || 'draft';

          const versionEl = document.getElementById('viewLessonVersion');
          if (versionEl) versionEl.textContent = `v${lesson.version || 1}`;

          const createdEl = document.getElementById('viewLessonCreated');
          if (createdEl) createdEl.textContent = new Date(lesson.created_at || lesson.createdAt).toLocaleDateString();

          const subjectEl = document.getElementById('viewLessonSubject');
          if (subjectEl) subjectEl.textContent = lesson.subject || 'General';

          const gradeEl = document.getElementById('viewLessonGrade');
          if (gradeEl) gradeEl.textContent = lesson.grade || 'General';

          // Populate current version preview
          const rawContent = lesson.content || lesson.body || 'No content yet';
          const currentEl = document.getElementById('viewLessonCurrent');
          const isSourcePdfLesson = !!lesson.source_pdf_url;
          setViewLessonSourcePdfMode(isSourcePdfLesson);
          if (isSourcePdfLesson) {
            renderSourcePdfLesson(currentEl, lesson.source_pdf_url);
          } else {
            renderLessonMarkdown(currentEl, rawContent);
            syncExpandedLessonPreviewFromCurrent();
          }
          renderLessonSummaryText(lesson);
          renderConversationSummaryText(null);
          setConversationSummaryLoading(false);
          loadConversationSummaryForLesson(lesson);
          const lessonPanel = document.getElementById('viewLessonLessonPanel');
          if (lessonPanel && !lessonPanel.classList.contains('hidden')) {
            loadLessonSummaryForLesson(lesson);
          }

          // Load draft content from backend so edits persist across sessions
          const draftTextarea = document.getElementById('viewLessonDraft');
          if (draftTextarea && !isSourcePdfLesson) {
            let draftValue = '';
            try {
              const draftResponse = await fetch(`/api/lessons/lesson/${lesson.id}/get_draft`, {
                method: 'GET',
                credentials: 'include'
              });
              if (draftResponse.ok) {
                const draftData = await draftResponse.json().catch(function () { return {}; });
                // Only pre-fill if there is an existing draft; otherwise start empty.
                draftValue = draftData.draft_content || '';
                const published = String(lesson.content || lesson.body || '').trim();
                if (draftValue.trim() && draftValue.trim() === published) {
                  draftValue = '';
                }
              }
            } catch (e) {
              console.warn('get_draft error:', e);
            }
            setViewLessonDraftMarkdown(draftValue);
            // Finalize button should be hidden until there is draft content / edits.
            setFinalizeButtonVisible(!!String(draftValue || '').trim());
            draftTextarea.oninput = function () {
              // Manual DOM edits no longer match stored markdown — fall back to
              // editor text on save so user changes are not discarded.
              window._viewLessonDraftDirty = true;
              setFinalizeButtonVisible(true);
            };
          } else if (draftTextarea) {
            setViewLessonDraftMarkdown('');
            setFinalizeButtonVisible(false);
          }

          // ── Version history panel ──────────────────────────────────────────────
          const viewedVersion = lesson.version || 1;
          const latestVersionNum = lesson.latestVersionNum || viewedVersion;
          const isLatestVersion = lesson.isLatestVersion !== false;
          window.currentViewingVersionIsLatest = isLatestVersion;
          window.currentViewingVersion = viewedVersion;

          let versionsHtml = '';
          let latestVersion = null;

          if (lesson.versions && Array.isArray(lesson.versions) && lesson.versions.length > 0) {
            const sortedVersions = [...lesson.versions].sort((a, b) => (b.version || 0) - (a.version || 0));
            latestVersion = sortedVersions[0];

            versionsHtml = sortedVersions.map((v, idx) => {
              const vNum = v.version || idx + 1;
              const isViewing = vNum === viewedVersion;
              const isLatest = idx === 0;
              const dateStr = v.createdAt ? new Date(v.createdAt).toLocaleDateString() : '—';
              return `
      <div class="view-lesson-version-item ${isViewing ? 'current' : ''}"
           style="cursor:pointer"
           onclick="switchViewedVersion(${vNum})"
           title="${isLatest ? 'Current version' : 'Click to view this version'}">
        <div class="flex items-center justify-between gap-2">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-1.5">
              <strong>v${vNum}</strong>
              ${isLatest ? '<span class="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full font-semibold">Current</span>' : ''}
              ${isViewing && !isLatest ? '<span class="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full font-semibold">Viewing</span>' : ''}
            </div>
            <div class="text-xs mt-0.5 text-gray-500">${dateStr}</div>
          </div>
          <button onclick="event.stopPropagation(); loadVersionIntoDraft('${lesson.id}', ${vNum})"
                         class="flex-shrink-0 px-2 py-1 text-xs bg-gray-100 hover:bg-blue-100 text-gray-600 hover:text-blue-700 border border-gray-200 rounded transition-colors"
                         title="Load into draft editor">Edit</button>
        </div>
      </div>`;
            }).join('');
          } else {
            latestVersion = { version: lesson.version || 1, createdAt: lesson.createdAt || new Date().toISOString() };
            versionsHtml = `<div class="view-lesson-version-item current" style="cursor:default">
              <div class="flex items-center justify-between gap-2">
                <div class="flex-1 min-w-0">
                  <strong>v1</strong>
                  <span class="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full ml-1">Current</span>
                  <div class="text-xs mt-0.5 text-gray-500">Only version</div>
                </div>
                <button onclick="event.stopPropagation(); loadVersionIntoDraft('${lesson.id}', ${lesson.version || 1})"
                        class="flex-shrink-0 px-2 py-1 text-xs bg-gray-100 hover:bg-blue-100 text-gray-600 hover:text-blue-700 border border-gray-200 rounded transition-colors"
                        title="Load into draft editor">Edit</button>
              </div>
            </div>`;
          }

          // Update the dropdown button label
          const currentVersionDisplay = document.getElementById('currentVersionDisplay');
          if (currentVersionDisplay) {
            currentVersionDisplay.textContent = isLatestVersion
              ? `v${viewedVersion} (Current)`
              : `v${viewedVersion} (Older)`;
          }

          document.getElementById('viewLessonVersions').innerHTML = versionsHtml;

          // Setup dropdown toggle
          const dropdownBtn = document.getElementById('versionDropdownBtn');
          const versionsList = document.getElementById('viewLessonVersions');
          const dropdownIcon = document.getElementById('versionDropdownIcon');

          if (dropdownBtn && versionsList && dropdownIcon) {
            // Style the button to reflect whether viewing latest or older
            dropdownBtn.className = dropdownBtn.className
              .replace(/\bfrom-\S+\b|\bto-\S+\b|\bborder-\S+\b/g, '');
            if (isLatestVersion) {
              dropdownBtn.classList.add('from-emerald-50', 'to-emerald-100', 'border-emerald-300');
              dropdownBtn.classList.remove('from-amber-50', 'to-amber-100', 'border-amber-300');
            } else {
              dropdownBtn.classList.add('from-amber-50', 'to-amber-100', 'border-amber-300');
              dropdownBtn.classList.remove('from-emerald-50', 'to-emerald-100', 'border-emerald-300');
            }
            dropdownBtn.onclick = function () {
              versionsList.classList.toggle('hidden');
              dropdownIcon.classList.toggle('fa-chevron-down');
              dropdownIcon.classList.toggle('fa-chevron-up');
            };
          }

          // Show/hide older-version notice and restrict new version creation
          const finalizeBtn = document.getElementById('finalizeVersionBtn');
          setOlderVersionNotice(!isLatestVersion, viewedVersion, latestVersionNum);
          if (!isLatestVersion) {
            if (finalizeBtn) {
              finalizeBtn.classList.add('hidden');
              finalizeBtn.disabled = true;
            }
          } else {
            if (finalizeBtn) {
              // Restore finalize button to its draft-controlled state
              finalizeBtn.disabled = false;
              // setFinalizeButtonVisible controls the actual visibility based on draft edits
            }
          }

          // Load saved templates
          loadSavedTemplatesIntoModal();
          console.log('✅ populateViewLessonModal completed successfully');
        } catch (err) {
          console.error('❌ Error in populateViewLessonModal:', err);
          console.error('Stack:', err.stack);
          alert('Error loading lesson: ' + err.message);
        }
      }

      // Show the view lesson modal
      function showViewLessonModal() {
        console.log('📺 showViewLessonModal called');

        // Close OTHER modals but NOT viewLessonModal itself
        try {
          const setPromptModal = document.getElementById('setPromptModal');
          const createLessonModal = document.getElementById('createLessonModal');
          const saveTemplateModal = document.getElementById('saveTemplateModal');
          const testPromptModal = document.getElementById('testPromptModal');

          if (setPromptModal) setPromptModal.classList.add('hidden');
          if (createLessonModal) createLessonModal.classList.add('hidden');
          if (saveTemplateModal) saveTemplateModal.classList.add('hidden');
          if (testPromptModal) testPromptModal.classList.add('hidden');
          console.log('✅ Closed other modals');
        } catch (e) {
          console.error('❌ Error closing other modals:', e);
        }

        // Now show THIS modal
        const modal = document.getElementById('viewLessonModal');
        if (modal) {
          console.log('📺 Step 1: viewLessonModal element found');
          console.log('   - Element:', modal);
            // Ensure modal is placed at end of <body> to avoid stacking-context issues
            try {
              document.body.appendChild(modal);
            } catch (e) {
              console.warn('Could not append modal to body (already present?):', e);
            }

            // Step 1: Remove hidden class
            modal.classList.remove('hidden');
            console.log('📺 Step 2: Removed "hidden" class');
            console.log('   - Classes:', modal.getAttribute('class'));

            // Step 2: Add visible-modal class
            modal.classList.add('visible-modal');
            console.log('📺 Step 3: Added "visible-modal" class');
            console.log('   - Classes:', modal.getAttribute('class'));

            // Step 3: Force reflow
            const height = modal.offsetHeight;
            console.log('📺 Step 4: Forced reflow, offsetHeight:', height);

            // Step 4: Set inline style explicitly to guarantee visibility and pointer events
            modal.style.display = 'flex';
            modal.style.visibility = 'visible';
            modal.style.opacity = '1';
            modal.style.pointerEvents = 'auto';
            /* Stay below #toastContainer so Save Draft / errors are visible */
            modal.style.zIndex = '10040';
            modal.setAttribute('aria-hidden', 'false');
            console.log('📺 Step 5: Applied inline styles (zIndex=10040)');

          // Ensure expand action is bound even if inline handlers are blocked/overridden.
          const expandBtn = document.getElementById('expandLessonPreviewBtn');
          if (expandBtn) {
            expandBtn.onclick = function (e) {
              if (e) e.preventDefault();
              openExpandedLessonPreview();
            };
          }
          const panel = document.querySelector('#viewLessonModal .view-lesson-modal-panel');
          const panelIcon = document.getElementById('expandMainLessonModalIcon');
          const panelBtn = document.getElementById('expandMainLessonModalBtn');
          if (panel) panel.classList.add('is-maximized');
          if (panelIcon) {
            panelIcon.classList.remove('fa-expand');
            panelIcon.classList.add('fa-compress');
          }
          if (panelBtn) {
            panelBtn.title = 'Restore window size';
            panelBtn.setAttribute('aria-label', panelBtn.title);
          }
          
          // Step 5: Check what browser computed
          const computed = window.getComputedStyle(modal);
          console.log('📺 Step 6: Computed styles:');
          console.log('   - display:', computed.display);
          console.log('   - visibility:', computed.visibility);
          console.log('   - opacity:', computed.opacity);
          console.log('   - z-index:', computed.zIndex);
          console.log('   - position:', computed.position);
          console.log('   - top:', computed.top);
          console.log('   - left:', computed.left);
          
          // Step 6: Check if element is in DOM
          const inDOM = document.contains(modal);
          console.log('📺 Step 7: Element in DOM:', inDOM);
          
          // Step 7: Scroll to show it
          window.scrollTo(0, 0);
          console.log('📺 Step 8: Scrolled to top');
          
          // Step 8: Final verification
          setTimeout(() => {
            const finalComputed = window.getComputedStyle(modal);
            console.log('📺 FINAL CHECK (after 100ms):');
            console.log('   - display:', finalComputed.display);
            console.log('   - visibility:', finalComputed.visibility);
            console.log('   - opacity:', finalComputed.opacity);
            console.log('   - offsetHeight:', modal.offsetHeight);
            console.log('   - offsetWidth:', modal.offsetWidth);
            console.log('   - getBoundingClientRect:', modal.getBoundingClientRect());
          }, 100);
          
          console.log('✅ Modal display sequence completed');
        } else {
          console.error('❌ viewLessonModal element NOT FOUND!!!');
          alert('ERROR: Modal element not found in HTML!');
        }
      }

      // Close the view lesson modal
      function closeViewLessonModal() {
        closeExpandedLessonPreview();
        closeExpandedConversationSummary();
        const panel = document.querySelector('#viewLessonModal .view-lesson-modal-panel');
        const icon = document.getElementById('expandMainLessonModalIcon');
        const btn = document.getElementById('expandMainLessonModalBtn');
        if (panel) panel.classList.remove('is-maximized');
        if (icon) {
          icon.classList.add('fa-expand');
          icon.classList.remove('fa-compress');
        }
        if (btn) {
          btn.title = 'Expand window';
          btn.setAttribute('aria-label', 'Expand lesson modal');
        }
        closeAllModals(); // Close all modals to ensure clean state
        window.currentViewLessonId = null;
        window.currentViewedLessonData = null;
        conversationSummaryRequestToken += 1;
        lessonSummaryRequestToken += 1;
        setConversationSummaryLoading(false);
        setLessonSummaryLoading(false);
        renderConversationSummaryText(null);
        renderLessonSummaryText(null);
        setOlderVersionNotice(false);
        clearViewLessonDraftEditor();

        // Only show chat input area if Chat tab is currently active
        const chatTabBtn = document.getElementById('chatTabBtn');
        const chatInputArea = document.getElementById('chatInputArea');

        if (chatTabBtn && chatTabBtn.classList.contains('active-tab') && chatInputArea) {
          chatInputArea.style.display = 'block';

          // Use requestAnimationFrame to ensure browser layout calculations are done
          requestAnimationFrame(() => {
            setTimeout(() => {
              scrollToBottom();
              const input = document.getElementById('messageInput');
              if (input) input.focus();
            }, 10);
          });
        }
      }

      // Apply custom prompt to lesson draft
      async function applyPromptToLessonDraft() {
        const promptEl = document.getElementById('viewLessonPrompt');
        const userPrompt = (promptEl?.value || '').trim();

        if (!userPrompt) {
          showToast('Please enter instructions for AI before applying.', 'info', 3000);
          return;
        }

        const lessonId = window.currentViewLessonId;
        if (!lessonId) {
          showToast('No lesson selected for AI enhancement.', 'error', 3000);
          return;
        }

        const btn = document.querySelector('#viewLessonModal button[onclick="applyPromptToLessonDraft()"]');
        let originalHtml = null;
        if (btn) {
          originalHtml = btn.innerHTML;
          btn.disabled = true;
          btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Applying...';
        }

        try {
          const response = await fetch(`/api/lessons/lesson/${lessonId}/apply_prompt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ prompt: userPrompt })
          });

          const data = await response.json().catch(function () { return {}; });
          if (!response.ok || !data.success) {
            throw new Error(data.error || 'Failed to apply AI enhancement');
          }

          const improved = data.draft_content || '';
          const draftEl = document.getElementById('viewLessonDraft');
          if (draftEl) {
            // Keep markdown source of truth — do not rely on contenteditable
            // innerText later (it strips # / ** / list markers).
            setViewLessonDraftMarkdown(improved);
            setFinalizeButtonVisible(true);
          }

          showToast('AI enhancement applied to draft!', 'success', 2500);
          if (promptEl) {
            promptEl.value = '';
          }
        } catch (e) {
          console.error('applyPromptToLessonDraft error:', e);
          showToast(e.message || 'Failed to apply AI enhancement', 'error', 3000);
        } finally {
          if (btn && originalHtml !== null) {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
          }
        }
      }

      // Save the currently visible draft (persists to backend)
      async function saveLessonDraftVisible() {
        const lessonId = window.currentViewLessonId;
        if (!lessonId) return;

        const draftContent = getViewLessonDraftMarkdown();

        try {
          const response = await fetch(`/api/lessons/lesson/${lessonId}/save_draft`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ draft_content: draftContent })
          });

          const data = await response.json().catch(function () { return {}; });
          if (!response.ok || !data.success) {
            throw new Error(data.error || 'Failed to save draft');
          }

          // Keep local markdown in sync with what we just persisted.
          if (!window._viewLessonDraftDirty) {
            window._viewLessonDraftMarkdown = draftContent;
          }
          showToast('Draft saved!', 'success', 2000);
        } catch (e) {
          console.error('saveLessonDraftVisible error:', e);
          showToast(e.message || 'Failed to save draft', 'error', 3000);
        }
      }

      // Finalize new version (creates new version via backend)
      async function finalizeNewVersionLesson() {
        const lessonId = window.currentViewLessonId;
        if (!lessonId) return;

        const draftContent = getViewLessonDraftMarkdown();
        if (!draftContent) {
          showToast('Draft is empty. Please edit the content before finalizing.', 'warning', 3000);
          return;
        }

        if (!await showInAppConfirm(
          'Finalize this new version? This will create a new published version from the draft.',
          { confirmLabel: 'Finalize', cancelLabel: 'Cancel' }
        )) {
          return;
        }

        const finalizeBtn = document.querySelector('#viewLessonModal button[onclick="finalizeNewVersionLesson()"]');
        if (typeof setButtonLoading === 'function') {
          setButtonLoading(finalizeBtn, '<span class="flex items-center gap-2"><span class="loading-dots"><span></span><span></span><span></span></span><span>Finalizing...</span></span>');
        }

        try {
          // Persist markdown (not contenteditable innerText) so v2 keeps formatting.
          let response = await fetch(`/api/lessons/lesson/${lessonId}/save_draft`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ draft_content: draftContent })
          });
          let data = await response.json().catch(function () { return {}; });
          if (!response.ok || !data.success) {
            throw new Error(data.error || 'Failed to save draft before finalizing');
          }

          // Then ask backend to create a new version from that draft
          response = await fetch(`/api/lessons/lesson/${lessonId}/finalize_version`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({})
          });
          data = await response.json().catch(function () { return {}; });
          if (!response.ok || !data.success) {
            throw new Error(data.error || 'Failed to finalize lesson version');
          }

          showToast('✅ New version created successfully!', 'success', 3000);
          clearViewLessonDraftEditor();
          closeViewLessonModal();
          showMyLessonsPage();
        } catch (e) {
          console.error('finalizeNewVersionLesson error:', e);
          showToast(e.message || 'Failed to finalize version', 'error', 4000);
        } finally {
          if (typeof restoreButton === 'function') {
            restoreButton(finalizeBtn);
          }
        }
      }

      // Delete lesson - API: DELETE /api/lessons/lesson/:id
      async function deleteLesson(lessonId) {
        if (!confirm('Are you sure you want to permanently delete this lesson?')) return;
        try {
          const response = await fetch('/api/lessons/lesson/' + lessonId, { method: 'DELETE', credentials: 'include' });
          if (response.ok) {
            showToast('Lesson deleted successfully', 'success');
            showMyLessonsPage();
          } else {
            const err = await response.json().catch(function() { return {}; });
            showToast(err.error || 'Failed to delete lesson', 'error');
          }
        } catch (e) {
          showToast('Error deleting lesson', 'error');
        }
      }

      // Switch the content displayed in the view modal to a specific version number.
      // Uses the already-fetched window.currentLessonVersions — no extra API call needed.
      function switchViewedVersion(versionNumber) {
        const versions = window.currentLessonVersions || [];
        if (!versions.length) return;

        const sortedVersions = versions.slice().sort(function(a, b) {
          return (b.version_number || b.version || 0) - (a.version_number || a.version || 0);
        });
        const latestVersionNum = sortedVersions[0].version_number || sortedVersions[0].version || 1;
        const isLatest = versionNumber === latestVersionNum;

        const target = sortedVersions.find(function(v) {
          return (v.version_number || v.version || 1) === versionNumber;
        });
        if (!target) return;

        // Update preview content AND title: a new topic saved in the same chat becomes
        // v2 with its own title/content. Leaving the v2 title in place made v1 look
        // like the same Nature-of-Roots lesson.
        const titleEl = document.getElementById('viewLessonTitle');
        if (titleEl && (target.title || (window.currentViewedLessonData && window.currentViewedLessonData.title))) {
          titleEl.textContent = target.title || window.currentViewedLessonData.title || 'Untitled Lesson';
        }
        const currentEl = document.getElementById('viewLessonCurrent');
        const targetPdfUrl = target.source_pdf_url || (window.currentViewedLessonData && window.currentViewedLessonData.source_pdf_url) || null;
        setViewLessonSourcePdfMode(!!targetPdfUrl);
        if (currentEl && targetPdfUrl) {
          renderSourcePdfLesson(currentEl, targetPdfUrl);
        } else if (currentEl) {
          renderLessonMarkdown(currentEl, target.content || '');
          syncExpandedLessonPreviewFromCurrent();
        }
        if (window.currentViewedLessonData) {
          window.currentViewedLessonData.versionLessonId = target.id || window.currentViewedLessonData.versionLessonId;
          window.currentViewedLessonData.content = target.content || '';
          window.currentViewedLessonData.title = target.title || window.currentViewedLessonData.title;
          window.currentViewedLessonData.summary = target.summary || null;
          window.currentViewedLessonData.version = versionNumber;
          window.currentViewedLessonData.source_pdf_url = targetPdfUrl;
          window.currentViewedLessonData.source_view_mode = targetPdfUrl ? 'pdf' : null;
        }
        lessonSummaryRequestToken += 1;
        setLessonSummaryLoading(false);
        renderLessonSummaryText(window.currentViewedLessonData || target);
        const lessonPanel = document.getElementById('viewLessonLessonPanel');
        if (lessonPanel && !lessonPanel.classList.contains('hidden')) {
          loadLessonSummaryForLesson(window.currentViewedLessonData || target);
        }

        // Update version badge in header
        const versionEl = document.getElementById('viewLessonVersion');
        if (versionEl) versionEl.textContent = 'v' + versionNumber;

        // Update the dropdown button label
        const currentVersionDisplay = document.getElementById('currentVersionDisplay');
        if (currentVersionDisplay) {
          currentVersionDisplay.innerHTML =
            '<i class="fas fa-code-branch"></i> '
            + (isLatest ? 'v' + versionNumber + ' (Current)' : 'v' + versionNumber + ' (Older)');
        }

        // Highlight the selected version in the list
        document.querySelectorAll('#viewLessonVersions .view-lesson-version-item').forEach(function(el) {
          el.classList.remove('current');
        });
        const versionItems = document.querySelectorAll('#viewLessonVersions .view-lesson-version-item');
        sortedVersions.forEach(function(v, idx) {
          const vNum = v.version_number || v.version || 1;
          if (vNum === versionNumber && versionItems[idx]) {
            versionItems[idx].classList.add('current');
          }
        });

        // Manage finalize button and older-version notice
        const finalizeBtn = document.getElementById('finalizeVersionBtn');
        setOlderVersionNotice(!isLatest, versionNumber, latestVersionNum);

        const dropdownBtn = document.getElementById('versionDropdownBtn');
        if (!isLatest) {
          if (finalizeBtn) { finalizeBtn.classList.add('hidden'); finalizeBtn.disabled = true; }
          // Amber tint on dropdown button
          if (dropdownBtn) {
            dropdownBtn.classList.add('from-amber-50', 'to-amber-100', 'border-amber-300');
            dropdownBtn.classList.remove('from-emerald-50', 'to-emerald-100', 'border-emerald-300');
          }
        } else {
          if (finalizeBtn) { finalizeBtn.disabled = false; }
          // Restore green tint
          if (dropdownBtn) {
            dropdownBtn.classList.add('from-emerald-50', 'to-emerald-100', 'border-emerald-300');
            dropdownBtn.classList.remove('from-amber-50', 'to-amber-100', 'border-amber-300');
          }
        }

        window.currentViewingVersion = versionNumber;
        window.currentViewingVersionIsLatest = isLatest;
      }

      function loadVersionIntoDraft(lessonId, versionNumber) {
        const versions = window.currentLessonVersions || [];
        const version = versions.find(function (v) {
          return (v.version_number || v.version || 1) === versionNumber;
        });

        let versionContent = version && version.content ? version.content : null;
        if (!versionContent && window.currentViewedLessonData && window.currentViewedLessonData.content) {
          const currentV = window.currentViewedLessonData.version || 1;
          if (currentV === versionNumber) {
            versionContent = window.currentViewedLessonData.content;
          }
        }

        if (versionContent) {
          const draftTextarea = document.getElementById('viewLessonDraft');
          if (draftTextarea) {
            renderLessonMarkdown(draftTextarea, versionContent);
            draftTextarea.focus();
          }

          saveLessonDraftVisible();
          showToast(`✅ Loaded v${versionNumber} into draft editor. Make your changes and click "Finalize" to create a new version.`, 'success', 3500);
        } else {
          showToast(versions.length ? 'Version content not found.' : 'Version history not available for this lesson.', 'error', 2000);
        }
      }

      // Edit with human feedback
      function editWithHumanLessonModal() {
        // Focus on the draft textarea for manual editing
        document.getElementById('viewLessonDraft').focus();
        showToast('Ready for human editing. Make changes and click "Save Draft" or "Finalize New Version".', 'info', 3000);
      }

      // Tone selection for lesson modal
      let currentLessonTone = 'professional';

      function selectToneLessonModal(tone) {
        currentLessonTone = tone;

        // Update button states
        document.querySelectorAll('#viewLessonModal .tone-btn').forEach(btn => {
          btn.classList.remove('active-tone');
        });

        const selectedBtn = document.querySelector(`#viewLessonModal .tone-btn.${tone}`);
        if (selectedBtn) {
          selectedBtn.classList.add('active-tone');
        }

        showToast(`Tone: ${tone}`, 'info', 1500);
      }

      // Load saved templates into modal
      function loadSavedTemplatesIntoModal() {
        const container = document.getElementById('savedTemplatesLesson');
        if (!container) return;

        const templates = JSON.parse(localStorage.getItem('teacher_prompt_templates') || '[]');

        if (templates.length === 0) {
          container.innerHTML = '<p class="text-xs text-gray-500 py-2">No templates saved</p>';
          return;
        }

        container.innerHTML = templates.map(template => `
    <div class="p-2 bg-white border border-gray-300 rounded text-xs cursor-pointer hover:bg-blue-50 transition-colors" onclick="loadTemplateIntoLessonDraft('${template.id}')">
      <div class="font-medium text-gray-800">${template.name}</div>
      <div class="text-gray-600 truncate">${template.prompt.substring(0, 50)}...</div>
    </div>
  `).join('');
      }

      // Load template into draft
      function loadTemplateIntoLessonDraft(templateId) {
        const templates = JSON.parse(localStorage.getItem('teacher_prompt_templates') || '[]');
        const template = templates.find(t => t.id === templateId);

        if (template) {
          document.getElementById('viewLessonDraft').value = template.prompt;
          saveLessonDraftVisible();
          showToast(`Loaded template: ${template.name}`, 'success', 2000);
        }
      }

      function getFilenameFromContentDisposition(contentDisposition, fallbackName) {
        if (!contentDisposition) return fallbackName;
        const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;\n]+)/i);
        if (utf8Match && utf8Match[1]) {
          try {
            return decodeURIComponent(utf8Match[1].replace(/['"]/g, '').trim());
          } catch (_) {
            return utf8Match[1].replace(/['"]/g, '').trim();
          }
        }
        const simpleMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/i);
        return (simpleMatch && simpleMatch[1]) ? simpleMatch[1].replace(/['"]/g, '').trim() : fallbackName;
      }

      async function downloadLessonDocx(lessonId) {
        if (!lessonId) return;
        try {
          showToast('Preparing DOCX download...', 'info');
          const response = await fetch('/api/lessons/download_lesson/' + encodeURIComponent(String(lessonId)), { method: 'GET', credentials: 'include' });
          if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            showToast(data.error || 'Download failed', 'error');
            return;
          }
          const blob = await response.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          const cd = response.headers.get('Content-Disposition');
          a.download = getFilenameFromContentDisposition(cd, 'lesson_' + lessonId + '.docx');
          document.body.appendChild(a);
          a.click();
          URL.revokeObjectURL(url);
          a.remove();
          showToast('Lesson downloaded as DOCX!', 'success');
        } catch (e) {
          showToast('Error downloading DOCX', 'error');
        }
      }

      // Download lesson as PPT - API: GET /api/lessons/download_lesson_ppt/:id
      async function downloadLessonPPT(lessonId) {
        if (!lessonId) return;
        try {
          showToast('Preparing PPT download...', 'info');
          const response = await fetch('/api/lessons/download_lesson_ppt/' + encodeURIComponent(String(lessonId)), { method: 'GET', credentials: 'include' });
          if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            showToast(data.error || 'Download failed', 'error');
            return;
          }
          const blob = await response.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          const cd = response.headers.get('Content-Disposition');
          a.download = getFilenameFromContentDisposition(cd, 'lesson_' + lessonId + '.pptx');
          document.body.appendChild(a);
          a.click();
          URL.revokeObjectURL(url);
          a.remove();
          showToast('Lesson downloaded as PowerPoint!', 'success');
        } catch (e) {
          showToast('Error downloading PPT', 'error');
        }
      }

      function normalizeFaqQuestionText(question) {
        let text = String(question || '').toLowerCase().trim();
        if (!text) return '';

        const replacements = [
          [/\bwhat's\b/g, 'what is'],
          [/\bwhats\b/g, 'what is'],
          [/\bwho's\b/g, 'who is'],
          [/\bwhere's\b/g, 'where is'],
          [/\bhow's\b/g, 'how is'],
          [/\bit's\b/g, 'it is']
        ];
        replacements.forEach(function(pair) {
          text = text.replace(pair[0], pair[1]);
        });

        text = text.replace(/[^\w\s]/g, ' ').replace(/\s+/g, ' ').trim();
        text = text.replace(/^(what is\s+)?rephrased question\s+/, '').trim();
        text = text.replace(/^(please\s+)?(can|could|would)\s+you\s+(tell|explain|define|describe)\s+/, '');
        text = text.replace(/^(can|could)\s+you\s+tell\s+me\s+/, '');
        text = text.replace(/^please\s+/, '').trim();

        const prefixes = [
          'what is the meaning of ',
          'what is meaning of ',
          'what are the different types of ',
          'meaning of ',
          'define ',
          'explain ',
          'what is ',
          'what are '
        ];
        let core = text;
        for (let i = 0; i < prefixes.length; i++) {
          if (core.indexOf(prefixes[i]) === 0) {
            core = core.slice(prefixes[i].length).trim();
            break;
          }
        }
        core = core.replace(/^(a|an|the)\s+/, '').replace(/\s+/g, ' ').trim();
        if (!core) return text;
        return 'what is ' + core;
      }

      function formatFaqQuestionFromKey(questionKey) {
        const q = String(questionKey || '').trim();
        if (!q) return '';
        const withMark = q.endsWith('?') ? q : (q + '?');
        return withMark.charAt(0).toUpperCase() + withMark.slice(1);
      }

      function renderFaqAnswerContent(answer) {
        const text = String(answer || '');
        if (!text.trim()) {
          return '<p class="text-sm text-gray-500 italic">Answer unavailable.</p>';
        }
        try {
          if (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.formatChatResponse) {
            return TeacherChatFormatter.formatChatResponse(text);
          }
        } catch (e) {
          console.warn('TeacherChatFormatter.formatChatResponse failed for FAQ answer', e);
        }
        return escapeHtml(text).replace(/\n/g, '<br>');
      }

      function applyFaqAnswerFormatting(node) {
        if (!node) return;
        try {
          if (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.processRenderedContent) {
            TeacherChatFormatter.processRenderedContent(node);
            return;
          }
        } catch (e) {
          console.warn('TeacherChatFormatter.processRenderedContent failed for FAQ answer', e);
        }
      }

      function toggleFaqAnswer(button) {
        const card = button ? button.closest('.faq-modern-card') : null;
        if (!card) return;
        const answer = card.querySelector('.faq-modern-answer');
        const chevron = card.querySelector('.faq-modern-chevron');
        if (!answer) return;
        const isHidden = answer.classList.contains('hidden');
        if (isHidden) {
          answer.classList.remove('hidden');
          card.classList.add('ring-2', 'ring-primary-100');
          if (chevron) chevron.classList.add('rotate-180');
        } else {
          answer.classList.add('hidden');
          card.classList.remove('ring-2', 'ring-primary-100');
          if (chevron) chevron.classList.remove('rotate-180');
        }
      }

      function copyFaqQuestion(button) {
        try {
          const card = button ? button.closest('.faq-modern-card') : null;
          const titleEl = card ? card.querySelector('.faq-modern-question') : null;
          const text = titleEl ? String(titleEl.textContent || '').trim() : '';
          if (!text) return;
          navigator.clipboard.writeText(text).then(function() {
            showToast('Question copied', 'success', 1200);
          }).catch(function() {
            showToast('Copy failed', 'error', 1200);
          });
        } catch (e) {
          showToast('Copy failed', 'error', 1200);
        }
      }

      function mergeFrequentFaqs(rawFaqs) {
        const grouped = {};
        (Array.isArray(rawFaqs) ? rawFaqs : []).forEach(function(item) {
          const key = normalizeFaqQuestionText(item.question || item.question_key || '');
          if (!key) return;
          const count = Number(item.times_asked || item.count || 1) || 1;
          if (!grouped[key]) {
            grouped[key] = {
              question_key: key,
              count: 0,
              answer: item.answer || ''
            };
          }
          grouped[key].count += count;
          if (!grouped[key].answer && item.answer) {
            grouped[key].answer = item.answer;
          }
        });

        // Only include genuinely frequent questions (asked 2+ times).
        return Object.keys(grouped)
          .map(function(key) {
            return {
              question_key: key,
              question: formatFaqQuestionFromKey(key),
              count: grouped[key].count,
              times_asked: grouped[key].count,
              answer: grouped[key].answer || ''
            };
          })
          .filter(function(item) { return item.count >= 2; })
          .sort(function(a, b) { return b.count - a.count; });
      }

      // Show FAQ for lesson - API: GET /api/lessons/faqs/:id
      async function showLessonFAQ(lessonId) {
        if (!lessonId) return;
        try {
          const response = await fetch('/api/lessons/faqs/' + lessonId, { method: 'GET', credentials: 'include' });
          const data = response.ok ? await response.json() : { faqs: [] };
          const faqs = mergeFrequentFaqs(data.faqs || []);
          const lessonTitle = (window.availableLessons && window.availableLessons.find(function(l) { return String(l.id) === String(lessonId); })) ? (window.availableLessons.find(function(l) { return String(l.id) === String(lessonId); }).title) : 'Lesson';
          const totalAsks = faqs.reduce(function(sum, row) { return sum + Number(row.times_asked || row.count || 0); }, 0);
          const faqRows = faqs.length ? faqs.map(function(faq, idx) {
            const renderedAnswer = renderFaqAnswerContent(faq.answer);
            return `
              <div class="faq-modern-card faqm-card">
                <div class="faqm-card-top">
                  <button type="button"
                          onclick="toggleFaqAnswer(this)"
                          class="faqm-chev-btn"
                          aria-label="Toggle answer">
                    <i class="faq-modern-chevron fas fa-chevron-down"></i>
                  </button>
                  <div class="faqm-card-body">
                    <div class="faqm-card-head">
                      <h4 class="faq-modern-question faqm-question">${escapeHtml(faq.question || '')}</h4>
                      <span class="faqm-asks-badge">
                        <i class="fas fa-fire-alt"></i> ${faq.times_asked || faq.count || 1} asks
                      </span>
                    </div>
                    <div class="faqm-card-foot">
                      <span class="faqm-index">FAQ #${idx + 1}</span>
                      <span class="faqm-dot">&bull;</span>
                      <button type="button" onclick="copyFaqQuestion(this)" class="faqm-copy-btn">
                        <i class="far fa-copy"></i> Copy question
                      </button>
                    </div>
                  </div>
                </div>
                <div class="faq-modern-answer faqm-answer hidden">
                  <p class="faqm-answer-label">Answer</p>
                  <div class="prose prose-sm max-w-none text-gray-800 tex2jax_process">${renderedAnswer}</div>
                </div>
              </div>
            `;
          }).join('') : `
            <div class="faqm-empty">
              <div class="faqm-empty-circle">
                <div class="faqm-empty-circle-inner">
                  <svg width="40" height="40" viewBox="0 0 24 24" fill="none"><path d="M21 11.5a8.38 8.38 0 01-9 8.4 8.5 8.5 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 018.4-9h.5a8.48 8.48 0 018 8v.5z" stroke="var(--primary-color)" stroke-width="1.6"/><path d="M12 9v4M12 16h.01" stroke="var(--primary-color)" stroke-width="1.8" stroke-linecap="round"/></svg>
                </div>
              </div>
              <h3>No frequently asked questions yet</h3>
              <p>FAQs appear here after students ask similar questions more than once.</p>
            </div>
          `;
          const faqContent = `
      <div class="faqm-overlay" id="faqModalOverlay">
        <div class="faqm-modal">
          <div class="faqm-close-x" onclick="document.getElementById('faqModalOverlay').remove()" aria-label="Close FAQ dialog">&times;</div>
          <div class="faqm-top">
            <div class="faqm-icon3d">
              <div class="faqm-icon-base"></div>
              <div class="faqm-icon-card">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="#fff" stroke-width="1.8"/><path d="M9.5 9a2.5 2.5 0 015 .5c0 1.5-2.5 2-2.5 3.5" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="17" r="0.6" fill="#fff"/></svg>
              </div>
            </div>
            <div>
              <div class="faqm-label-tag">&bull; INSIGHTS</div>
              <h1>Frequently Asked Questions</h1>
              <div class="faqm-sub">${escapeHtml(lessonTitle)}</div>
            </div>
          </div>

          <div class="faqm-stats">
            <div class="faqm-stat-card">
              <div class="faqm-stat-icon"><i class="fas fa-layer-group"></i></div>
              <div><div class="faqm-stat-num">${faqs.length}</div><div class="faqm-stat-label">FAQ topics</div></div>
            </div>
            <div class="faqm-stat-card">
              <div class="faqm-stat-icon"><i class="fas fa-chart-line"></i></div>
              <div><div class="faqm-stat-num">${totalAsks}</div><div class="faqm-stat-label">repeated asks</div></div>
            </div>
          </div>

          <div class="faqm-rows">${faqRows}</div>

          <div class="faqm-footer-bar">
            <div class="faqm-footer-note">
              <div class="faqm-hex-badge"><svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 2l1.5 3 3.3.5-2.4 2.3.6 3.3L12 9.5 9 11.1l.6-3.3L7.2 5.5l3.3-.5L12 2z" fill="#fff"/></svg></div>
              <span>Only recurring student questions are shown.</span>
            </div>
            <button type="button" onclick="document.getElementById('faqModalOverlay').remove()" class="faqm-close-btn">Close</button>
          </div>
        </div>
      </div>
    `;

          const existingFaqModal = document.getElementById('faqModalOverlay');
          if (existingFaqModal) existingFaqModal.remove();

          const container = document.createElement('div');
          container.innerHTML = faqContent;
          const modalEl = container.firstElementChild;
          document.body.appendChild(modalEl);
          if (modalEl) {
            modalEl.querySelectorAll('.prose').forEach(function(el) {
              applyFaqAnswerFormatting(el);
            });
            modalEl.addEventListener('click', function(event) {
              if (event.target && event.target.id === 'faqModalOverlay') {
                modalEl.remove();
              }
            });
          }
          showToast('FAQ opened', 'info', 1200);
        } catch (e) {
          console.error('showLessonFAQ failed', e);
          showToast('Failed to load FAQs', 'error');
        }
      }

      function editLesson(lessonId) {
        const lessons = window.availableLessons || [];
        const lesson = lessons.find(function (l) { return String(l.id) === String(lessonId); });

        if (lesson) {
          const subject = lesson.focus_area || lesson.focusArea || 'General';
          const rawGrade = String(lesson.grade_level || lesson.gradeLevel || '').trim();
          const gradeDigits = (rawGrade.match(/\d{1,2}/) || [])[0] || '';
          const grade = (gradeDigits && Number(gradeDigits) >= 1 && Number(gradeDigits) <= 12)
            ? String(Number(gradeDigits))
            : '';
          const gradeOptions = [1,2,3,4,5,6,7,8,9,10,11,12].map(function (n) {
            const label = n === 1 ? '1st Grade' : n === 2 ? '2nd Grade' : n === 3 ? '3rd Grade' : (n + 'th Grade');
            return '<option value="' + n + '"' + (String(grade) === String(n) ? ' selected' : '') + '>' + label + '</option>';
          }).join('');
          const legacyGradeOption = (!grade && rawGrade)
            ? ('<option value="' + rawGrade.replace(/"/g, '&quot;') + '" selected>' + rawGrade.replace(/</g, '') + ' (current)</option>')
            : '';

          // Show edit modal
          const modalHtml = `
          <div id="editLessonModal-${lessonId}" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 elm-overlay">
            <div class="elm-modal">
              <div class="elm-head">
                <div class="elm-close-x" onclick="document.getElementById('editLessonModal-${lessonId}').remove()"><i class="fas fa-times"></i></div>
                <div class="elm-head-row">
                  <div class="elm-head-icon"><i class="fas fa-pen"></i></div>
                  <div>
                    <h1>Edit Lesson</h1>
                    <p>Update your lesson details and save your changes.</p>
                  </div>
                </div>
              </div>

              <div class="elm-body">
                <div class="elm-field">
                  <div class="elm-field-icon"><i class="fas fa-heading"></i></div>
                  <div class="elm-field-content">
                    <label>Lesson Title</label>
                    <input type="text" id="editLessonTitle" value="${lesson.title || ''}">
                  </div>
                </div>

                <div class="elm-field">
                  <div class="elm-field-icon"><i class="fas fa-graduation-cap"></i></div>
                  <div class="elm-field-content">
                    <label>Subject</label>
                    <select id="editLessonSubject">
                      <option value="General" ${subject === 'General' ? 'selected' : ''}>General</option>
                      <option value="Math" ${subject === 'Math' ? 'selected' : ''}>Mathematics</option>
                      <option value="Science" ${subject === 'Science' ? 'selected' : ''}>Science</option>
                      <option value="English" ${subject === 'English' ? 'selected' : ''}>English</option>
                      <option value="History" ${subject === 'History' ? 'selected' : ''}>History</option>
                    </select>
                  </div>
                </div>

                <div class="elm-field">
                  <div class="elm-field-icon"><i class="fas fa-layer-group"></i></div>
                  <div class="elm-field-content">
                    <label>Grade Level</label>
                    <select id="editLessonGrade">
                      <option value="">Select grade</option>
                      ${legacyGradeOption}
                      ${gradeOptions}
                    </select>
                  </div>
                </div>
              </div>

              <div class="elm-footer">
                <div class="elm-footer-logo">${window.TEACHER_CFG.logoDark}</div>
                <div class="elm-footer-btns">
                  <button class="elm-btn-save" onclick="saveLessonEdit('${lessonId}')">
                    <i class="fas fa-save"></i> Save Changes
                  </button>
                  <button class="elm-btn-cancel" onclick="document.getElementById('editLessonModal-${lessonId}').remove()">
                    <i class="fas fa-times"></i> Cancel
                  </button>
                </div>
              </div>
            </div>
          </div>
        `;

          const modalContainer = document.createElement('div');
          modalContainer.innerHTML = modalHtml;
          document.body.appendChild(modalContainer);
        }
      }

      async function saveLessonEdit(lessonId) {
        const title = (document.getElementById('editLessonTitle')?.value || '').trim();
        const subject = document.getElementById('editLessonSubject')?.value || 'General';
        const grade = (document.getElementById('editLessonGrade')?.value || '').trim();

        if (!title) {
          showToast('Lesson title is required.', 'warning', 3000);
          return;
        }
        if (!grade) {
          showToast('Grade is required.', 'warning', 3000);
          return;
        }

        const saveBtn = document.querySelector(`#editLessonModal-${lessonId} button[onclick="saveLessonEdit('${lessonId}')"]`);
        if (typeof setButtonLoading === 'function') {
          setButtonLoading(saveBtn, '<span class="loading-dots"><span></span><span></span><span></span></span>');
        }

        try {
          const response = await fetch(`/api/lessons/lesson/${lessonId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
              title: title,
              focus_area: subject,
              grade_level: grade
            })
          });

          const data = await response.json().catch(function () { return {}; });
          if (!response.ok || !data.success) {
            throw new Error(data.error || 'Failed to update lesson');
          }

          const modalEl = document.getElementById(`editLessonModal-${lessonId}`);
          if (modalEl) {
            modalEl.remove();
          }

          showToast(`✅ Lesson "${title}" updated successfully!`, 'success', 4000);
          showMyLessonsPage();
        } catch (e) {
          console.error('saveLessonEdit error:', e);
          showToast(e.message || 'Failed to update lesson', 'error', 4000);
        } finally {
          if (typeof restoreButton === 'function') {
            restoreButton(saveBtn);
          }
        }
      }

      function deleteAllLessonsConfirm() {
        if (confirm('Are you sure you want to delete ALL lessons? This action cannot be undone.')) {
          localStorage.removeItem('teacher_lessons');
          showMyLessonsPage();
          showToast('🗑️ All lessons deleted', 'warning', 4000);
          addAssistantMessage('✅ All lessons have been deleted.');
        }
      }

      function exportAllLessons() {
        const lessons = JSON.parse(localStorage.getItem('teacher_lessons') || '[]');
        if (lessons.length === 0) {
          showToast('❌ No lessons to export', 'error', 3000);
          return;
        }

        const dataStr = JSON.stringify(lessons, null, 2);
        const dataBlob = new Blob([dataStr], { type: 'application/json' });
        const url = URL.createObjectURL(dataBlob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `lessons-export-${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);

        showToast(`✅ Exported ${lessons.length} lesson(s) successfully!`, 'success', 4000);
        addAssistantMessage('✅ All lessons exported successfully!');
      }

      // Pagination functions
      let currentPage = 1;
      const lessonsPerPage = 10;
      let teacherLessonsTotalPages = 1;
      let teacherLessonsTotalCount = 0;
      let teacherLessonSearchTerm = '';

      function updatePagination() {
        const totalPages = Math.max(1, teacherLessonsTotalPages);
        const prevBtn = document.getElementById('prevBtn');
        const nextBtn = document.getElementById('nextBtn');
        const pageInfo = document.getElementById('pageInfo');
        if (prevBtn) prevBtn.disabled = currentPage === 1;
        if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
        if (pageInfo) pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
      }

      function previousPage() {
        if (currentPage > 1) {
          currentPage--;
          showMyLessonsPage();
        }
      }

      function nextPage() {
        if (currentPage < Math.max(1, teacherLessonsTotalPages)) {
          currentPage++;
          showMyLessonsPage();
        }
      }

      // Helper function for lesson actions
      function generateQuizFromLesson(lessonId) {
        const index = findLessonById(lessonId);
        let lessons = JSON.parse(localStorage.getItem('teacher_lessons') || '[]');
        if (index !== -1 && lessons[index]) {
          const lesson = lessons[index];
          document.getElementById('messageInput').value = `Generate a quiz from the lesson: ${lesson.title}`;
          autoResizeTextarea(document.getElementById('messageInput'));
          updateSendButton();
          document.getElementById('messageInput').focus();
        }
      }

      function shareLesson(lessonId) {
        const index = findLessonById(lessonId);
        let lessons = JSON.parse(localStorage.getItem('teacher_lessons') || '[]');
        if (index !== -1 && lessons[index]) {
          const lesson = lessons[index];
          const modalHtml = `
          <div class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div class="bg-white rounded-xl p-6 max-w-md w-full mx-4">
              <div class="flex items-center justify-between mb-4">
                <h3 class="text-lg font-semibold text-gray-900">Share Lesson</h3>
                <button onclick="this.parentElement.parentElement.parentElement.remove()" 
                        class="text-gray-400 hover:text-gray-600">
                  <i class="fas fa-times"></i>
                </button>
              </div>
              
              <div class="mb-6">
                <p class="text-gray-700 mb-4">Share <strong>"${lesson.title}"</strong> with other teachers or students.</p>
                
                <div class="mb-4">
                  <label class="block text-sm font-medium text-gray-700 mb-2">Share Link</label>
                  <div class="flex gap-2">
                    <input type="text" 
                           id="shareLink" 
                           value="https://iqbalai.com/lesson/${lesson.id}"
                           readonly
                           class="flex-1 px-3 py-2 border border-gray-300 rounded-lg bg-gray-50">
                    <button onclick="copyShareLink()" 
                            class="bg-primary-600 text-white px-4 py-2 rounded-lg hover:bg-primary-700 transition-colors">
                      Copy
                    </button>
                  </div>
                </div>
                
                <div class="mb-4">
                  <label class="block text-sm font-medium text-gray-700 mb-2">Share via Email</label>
                  <input type="email" 
                         id="shareEmail" 
                         placeholder="Enter email address"
                         class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500">
                </div>
              </div>
              
              <div class="flex gap-3">
                <button onclick="sendLessonViaEmail('${lessonId}')" 
                        class="flex-1 bg-primary-600 text-white py-2.5 px-4 rounded-lg hover:bg-primary-700 transition-colors">
                  Send Email
                </button>
                <button onclick="this.parentElement.parentElement.parentElement.remove()" 
                        class="flex-1 bg-gray-200 text-gray-800 py-2.5 px-4 rounded-lg hover:bg-gray-300 transition-colors">
                  Close
                </button>
              </div>
            </div>
          </div>
        `;

          const modalContainer = document.createElement('div');
          modalContainer.innerHTML = modalHtml;
          document.body.appendChild(modalContainer);
        }
      }

      function copyShareLink() {
        const shareLink = document.getElementById('shareLink');
        shareLink.select();
        document.execCommand('copy');
        alert('Link copied to clipboard!');
      }

      function sendLessonViaEmail(lessonId) {
        const email = document.getElementById('shareEmail').value;
        if (!email) {
          alert('Please enter an email address.');
          return;
        }

        // In a real app, you would send this to your backend
        alert(`Lesson share email sent to ${email}`);
        document.querySelector('[onclick="sendLessonViaEmail(\'' + lessonId + '\')"]').closest('.fixed').remove();
      }

      // Start new chat
      // Auto-generate chat title from first message (max 20 chars)
      function generateChatTitle(message) {
        const maxLength = 20;
        try {
          if (!message) return 'New Conversation';
          const cleanedMessage = message.replace(/^\s+|\s+$/g, '').substring(0, maxLength);
          return cleanedMessage || 'New Conversation';
        } catch (e) {
          return 'New Conversation';
        }
      }

      // Update chat title after first message
      function updateChatTitle(chatId, newTitle) {
        const chat = chatHistory.find(c => c.id === chatId);
        if (chat && chat.title === 'New Conversation') {
          chat.title = newTitle;
          saveChatThreads();
          renderChatSidebar();
        }
      }

      function normalizeLessonSidebarTitle(lessonName) {
        let base = (lessonName || '').toString().trim();
        if (!base) base = 'New Conversation';
        base = base.replace(/^chat\s*:/i, '').trim();
        return `Chat: ${base || 'New Conversation'}`;
      }

      async function setBackendConversationTitle(conversationId, lessonName) {
        if (conversationId == null) return;
        const title = normalizeLessonSidebarTitle(lessonName);
        try {
          const response = await fetch('/update_conversation_title/' + conversationId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ title: title })
          });
          if (!response.ok) return;
          const chat = chatHistory.find(c => String(c.id) === String(conversationId));
          if (chat) {
            chat.title = title;
            renderChatSidebar();
          }
        } catch (e) {
          console.error('Failed to set conversation title', e);
        }
      }

      function startNewChat() {
        // ════════════════════════════════════════════════════════════════════════════════
        // SPECIFICATION #3: CHAT FINALIZATION
        // Finalize current chat before creating new one
        // ════════════════════════════════════════════════════════════════════════════════

        // Mark previous chat as inactive (if exists)
        chatHistory.forEach(chat => {
          chat.active = false;
        });
        saveChatThreads();

        // ════════════════════════════════════════════════════════════════════════════════
        // SPECIFICATION #1: NEW CHAT INITIALIZATION
        // Create new unique conversation ID with empty context
        // ════════════════════════════════════════════════════════════════════════════════

        // 1. Generate unique conversation ID
        const chatId = 'chat_' + Date.now();
        currentChatId = chatId;

        // 2. Initialize empty message array (scoped to this chat ID)
        // Messages will be stored in: localStorage['teacher_chat_messages_' + chatId]

        // 3. Create new chat object with initial state
        const newChat = {
          id: chatId,
          title: 'New Conversation',  // Auto-generated from first message later
          timestamp: new Date().toISOString(),
          active: true,  // This is the only active chat
          lastMessageSnippet: '',
          unreadCount: 0
        };

        // 4. Add to beginning of history (most recent first)
        chatHistory.unshift(newChat);
        saveChatThreads();
        renderChatSidebar();

        // 5. Initialize empty messages array for this chat in localStorage
        try {
          saveMessagesForChat(chatId, []);
        } catch (e) {
          console.error('Failed to initialize messages for new chat', e);
        }

        // ════════════════════════════════════════════════════════════════════════════════
        // SPECIFICATION #2: MESSAGE ISOLATION
        // Clear any previous context - start completely fresh
        // All messages will be stored ONLY under this new chatId
        // ════════════════════════════════════════════════════════════════════════════════
        clearChatContext();

        // Update UI
        updateChatHistoryUI();
        closeAllDropdowns();

        // Start new chats without a pre-populated welcome card.
        const chatMessages = document.getElementById('chatMessages');
        chatMessages.innerHTML = '';

        scrollToBottom();
        document.getElementById('messageInput').focus();
      }

      // ════════════════════════════════════════════════════════════════════════════════
      // SPECIFICATION #5: REOPENING A CHAT
      // Load only that chat's messages, clear previous context
      // ════════════════════════════════════════════════════════════════════════════════
      function loadChat(chatId) {
        window._loadChatPromise = Promise.resolve();
        window._loadChatResolve = null;
        // Ensure "My Lessons" is not highlighted while a chat is open.
        deactivateAllTabs();
        // 1. Clear previous context (no carryover from other chats)
        clearChatContext();

        // 2. Set this chat as the ONLY active chat
        currentChatId = chatId;
        // Also align RAG conversation ID so subsequent messages use this conversation
        try {
          window.currentRAGConversationId = chatId ? parseInt(chatId, 10) || chatId : null;
          localStorage.setItem(getLastConversationKey(), String(window.currentRAGConversationId));
        } catch (e) {
          console.error('Failed to persist last conversation id', e);
        }
        chatHistory.forEach(chat => {
          chat.active = chat.id === chatId;
        });
        // Clear unread count for this chat when opened
        const opened = chatHistory.find(c => c.id === chatId);
        if (opened) {
          opened.unreadCount = 0;
        }
        saveChatThreads();

        // Update UI
        updateChatHistoryUI();
        closeAllDropdowns();

        // ════════════════════════════════════════════════════════════════════════════════
        // SPECIFICATION #2: MESSAGE ISOLATION
        // Load ONLY messages from this specific conversation ID
        // Backend /get_messages/<id> expects integer conversation_id; client-only ids (chat_*) use localStorage.
        // NO context from other conversations
        // ════════════════════════════════════════════════════════════════════════════════

        // Load chat content
        const chatMessages = document.getElementById('chatMessages');
        chatMessages.innerHTML = '';

        function renderMessageDOM(role, content) {
          if (role === 'user') {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'chat-message user animate-fade-in';
            messageDiv.innerHTML = `
            <div class="chat-message-avatar">
              <div class="w-full h-full flex items-center justify-center bg-blue-100 text-blue-700 font-bold">G</div>
            </div>
            <div class="chat-message-content">${escapeHtml(content)}</div>
          `;
            chatMessages.appendChild(messageDiv);
            return null;
          } else {
            // Convert raw markdown to HTML (same as addAssistantMessage flow) so loaded messages render correctly
            const formattedContent = (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.formatChatResponse)
              ? TeacherChatFormatter.formatChatResponse(content || '') : escapeHtml(content || '');
            const messageDiv = document.createElement('div');
            messageDiv.className = 'chat-message assistant animate-fade-in';
            messageDiv.innerHTML = `
            <div class="chat-message-avatar">
              <img src="${window.TEACHER_CFG.aiIconUrl}" alt="AI Assistant" onerror="this.onerror=null; this.src=''; this.innerHTML='<i class=\\'fas fa-graduation-cap\\'></i>'; this.classList.add('flex', 'items-center', 'justify-center')">
            </div>
            <div class="chat-message-content markdown-content tex2jax_process">${formattedContent}</div>
          `;
            chatMessages.appendChild(messageDiv);
            var contentEl = messageDiv.querySelector('.chat-message-content');
            if (contentEl && typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.processRenderedContent) {
              // Returns a Promise that resolves after MathJax/KaTeX typesetting has settled,
              // so the caller can wait for all of these before scrolling to the true bottom.
              return TeacherChatFormatter.processRenderedContent(contentEl);
            }
            return null;
          }
        }

        // Only call backend when chatId is a backend conversation (integer). Client-only ids (chat_*) use localStorage.
        const isBackendConversationId = (function() {
          if (typeof chatId === 'number' && Number.isInteger(chatId)) return true;
          if (typeof chatId === 'string' && chatId.startsWith('chat_')) return false;
          const n = parseInt(chatId, 10);
          return !isNaN(n) && String(n) === String(chatId);
        })();

        function renderRAGPreamble(uploadedFilename, extra) {
          extra = extra || {};
          const pdfMeta = (typeof currentPDFData !== 'undefined' && currentPDFData) ? currentPDFData : {};
          const successHtml = buildPDFUploadSuccessMessage({
            fileName: uploadedFilename || pdfMeta.fileName || 'document',
            lessonTitle: pdfMeta.title || null,
            lessonSubject: pdfMeta.subject || null,
            lessonGrade: pdfMeta.grade || null,
            lessonContext: pdfMeta.context || null,
            fileSize: pdfMeta.fileSize || null,
            pages: extra.numPages != null ? extra.numPages : (pdfMeta.numPages || null)
          });
          chatMessages.innerHTML = `
        <div class="chat-message assistant animate-fade-in">
          <div class="chat-message-avatar">
            <img src="${window.TEACHER_CFG.aiIconUrl}" alt="AI Assistant" onerror="this.onerror=null; this.src=''; this.innerHTML='<i class=\\'fas fa-graduation-cap\\'></i>'; this.classList.add('flex', 'items-center', 'justify-center')">
          </div>
          <div class="chat-message-content">
            ${successHtml}
          </div>
        </div>
        `;
        }

        function applyLoadedMessages(messages, options) {
          options = options || {};
          const prependRAG = options.prependRAGPreamble && options.uploadedFilename !== undefined;
          chatMessages.innerHTML = '';
          if (prependRAG) {
            renderRAGPreamble(options.uploaded_filename || options.uploadedFilename || 'document', {
              numPages: options.num_pages != null ? options.num_pages : options.numPages
            });
          }
          let effectiveMessages = Array.isArray(messages) ? messages.slice() : [];

          // Keep the opening screen clean on RAG threads:
          // remove bootstrap upload/context/success chatter that duplicates preamble.
          if (prependRAG && effectiveMessages.length > 0) {
            effectiveMessages = effectiveMessages.filter(function (msg) {
              const roleRaw = String(msg.role || '').toLowerCase();
              const role = roleRaw === 'bot' ? 'assistant' : roleRaw;
              const content = String(msg.content || msg.message || '').trim();
              if (!content) return false;

              // User-side bootstrap messages
              if (role === 'user') {
                if (/^📚\s*(uploaded|uploading)\s+pdf:/i.test(content)) return false;
                if (/^📝\s*context:/i.test(content)) return false;
              }

              // Assistant-side bootstrap messages
              if (role === 'assistant') {
                if (/successfully uploaded/i.test(content)) return false;
                if (/pdf uploaded(\.|!)/i.test(content)) return false;
                if (/i can now answer questions based on this material/i.test(content)) return false;
                if (/what would you like to do\?/i.test(content)) return false;
              }

              return true;
            });
          }

          const renderPromises = [];
          if (effectiveMessages.length > 0) {
            effectiveMessages.forEach(function (msg) {
              const role = msg.role === 'bot' ? 'assistant' : 'user';
              const content = msg.content || msg.message || '';
              renderPromises.push(renderMessageDOM(role, content));
            });
          } else if (!prependRAG) {
            renderPromises.push(renderMessageDOM('assistant', 'Welcome to your saved conversation. How can I continue assisting you with your teaching?'));
          }
          // Wait for all messages' math typesetting to settle before measuring scrollHeight —
          // otherwise the last message (often the one with equations) can still be resizing
          // when we scroll, leaving the view short of the true bottom.
          Promise.all(renderPromises).then(function () {
            requestAnimationFrame(function () { scrollToBottom('auto'); });
          });
        }

        if (isBackendConversationId) {
          // Load from backend (integer conversation_id)
          const numericId = typeof chatId === 'number' ? chatId : parseInt(chatId, 10);
          window._loadChatResolve = null;
          window._loadChatPromise = new Promise(function (resolve) { window._loadChatResolve = resolve; });
          fetch(`/get_messages/${numericId}`, {
            method: 'GET',
            credentials: 'include'
          }).then(function (res) {
            if (!res.ok) throw new Error('Failed to load messages');
            return res.json();
          }).then(function (data) {
            if (data && data.thread_id) {
              window.currentRAGThreadId = data.thread_id;
              try { localStorage.setItem('teacher_currentRAGThreadId', data.thread_id); } catch (e) {}
            }
            var messages = (data && data.messages) || [];
            var opts = {};
            if (data && data.thread_id) {
              opts.prependRAGPreamble = true;
              opts.uploaded_filename = data.uploaded_filename || null;
              opts.uploadedFilename = data.uploaded_filename || null;
              opts.num_pages = data.num_pages != null ? data.num_pages : null;
              opts.numPages = data.num_pages != null ? data.num_pages : null;
            }
            applyLoadedMessages(messages, opts);
            refreshSaveLessonAvailability();
            if (window._loadChatResolve) { window._loadChatResolve(); window._loadChatResolve = null; }
          }).catch(function (err) {
            console.error('Error loading chat messages', err);
            applyLoadedMessages([]);
            renderMessageDOM('assistant', 'Unable to load previous messages for this conversation.');
            refreshSaveLessonAvailability();
            if (window._loadChatResolve) { window._loadChatResolve(); window._loadChatResolve = null; }
          });
        } else {
          // Client-only chat (e.g. chat_1771259540797): load from localStorage
          try {
            const messages = loadMessagesForChat(chatId);
            applyLoadedMessages(messages);
          } catch (e) {
            console.error('Error loading chat messages from localStorage', e);
            applyLoadedMessages([]);
          }
        }

        // Show chat input area, floating button, and hide prompt area
        const chatInputArea = document.getElementById('chatInputArea');
        const floatingBtn = document.getElementById('floatingActionBtn');
        const promptArea = document.getElementById('promptArea');

        if (chatInputArea) chatInputArea.style.display = 'block';
        if (floatingBtn) floatingBtn.classList.remove('hidden-fab');
        if (promptArea) promptArea.style.display = 'none';

        scrollToBottom();
        document.getElementById('messageInput').focus();
        // Resolve promise for client-only path (backend path resolves in fetch .then/.catch)
        if (!isBackendConversationId && window._loadChatResolve) {
          window._loadChatResolve();
          window._loadChatResolve = null;
        }
        refreshSaveLessonAvailability();
      }

      // Returns a promise that resolves when messages have been applied (for backend chats). Client-only chats resolve immediately after applyLoadedMessages.
      function loadChatPromise(chatId) {
        loadChat(chatId);
        return window._loadChatPromise || Promise.resolve();
      }

      // Update chat history UI
      function updateChatHistoryUI() {
        // Update active states visually
        document.querySelectorAll('.chat-history-item').forEach(item => {
          item.classList.remove('active');
        });

        const activeItem = document.querySelector(`[onclick="loadChat('${currentChatId}')"]`);
        if (activeItem) {
          activeItem.classList.add('active');
        }
      }

      // Rename chat
      function renameChat() {
        if (!currentChatMenuId) return;

        const newTitle = prompt('Enter new chat title:',
          chatHistory.find(chat => chat.id === currentChatMenuId)?.title || 'New Chat');

        if (!newTitle || !newTitle.trim()) {
          closeAllDropdowns();
          return;
        }

        const trimmedTitle = newTitle.trim();

        // Decide if this is a backend conversation (numeric id) or a client-only chat_* id
        const isBackendConversationId = (function () {
          if (typeof currentChatMenuId === 'number' && Number.isInteger(currentChatMenuId)) return true;
          if (typeof currentChatMenuId === 'string' && currentChatMenuId.startsWith('chat_')) return false;
          const n = parseInt(currentChatMenuId, 10);
          return !isNaN(n) && String(n) === String(currentChatMenuId);
        })();

        if (isBackendConversationId) {
          // Call backend to persist the new title
          fetch('/update_conversation_title/' + currentChatMenuId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ title: trimmedTitle })
          }).then(function (response) {
            if (!response.ok) {
              return response.json().catch(function () { return {}; }).then(function (data) {
                throw new Error(data.error || 'Failed to update title');
              });
            }
            return response.json();
          }).then(function () {
            const chat = chatHistory.find(function (c) { return String(c.id) === String(currentChatMenuId); });
            if (chat) {
              chat.title = trimmedTitle;
              saveChatThreads();
              renderChatSidebar();
            }
            const chatElement = document.querySelector(`[onclick="loadChat('${currentChatMenuId}')"] .text-gray-900`);
            if (chatElement) {
              chatElement.textContent = trimmedTitle;
            }
          }).catch(function (err) {
            console.error('Failed to rename conversation', err);
            if (typeof showToast === 'function') {
              showToast('Failed to rename chat: ' + err.message, 'error', 3000);
            }
          }).finally(function () {
            closeAllDropdowns();
          });
        } else {
          // Fallback: client-only chat, keep existing local behavior
          const chat = chatHistory.find(chat => chat.id === currentChatMenuId);
          if (chat) {
            chat.title = trimmedTitle;
            saveChatThreads();
            renderChatSidebar(); // Update sidebar with renamed chat

            // Update UI
            const chatElement = document.querySelector(`[onclick="loadChat('${currentChatMenuId}')"] .text-gray-900`);
            if (chatElement) {
              chatElement.textContent = trimmedTitle;
            }
          }
          closeAllDropdowns();
        }
      }

      // Duplicate chat
      function duplicateChat() {
        if (!currentChatMenuId) return;

        const originalChat = chatHistory.find(chat => chat.id === currentChatMenuId);
        if (originalChat) {
          const isBackendConversationId = (function () {
            if (typeof originalChat.id === 'number' && Number.isInteger(originalChat.id)) return true;
            if (typeof originalChat.id === 'string' && originalChat.id.startsWith('chat_')) return false;
            const n = parseInt(originalChat.id, 10);
            return !isNaN(n) && String(n) === String(originalChat.id);
          })();

          if (isBackendConversationId) {
            // Ask backend to duplicate the conversation including messages
            fetch('/duplicate_conversation/' + originalChat.id, {
              method: 'POST',
              credentials: 'include'
            }).then(function (response) {
              if (!response.ok) {
                return response.json().catch(function () { return {}; }).then(function (data) {
                  throw new Error(data.error || 'Failed to duplicate conversation');
                });
              }
              return response.json();
            }).then(function (data) {
              const conv = data.conversation || {};
              const newChat = {
                id: conv.id,
                title: conv.title || (originalChat.title + ' (Copy)'),
                timestamp: conv.updated_at || conv.created_at || new Date().toISOString(),
                active: false,
                lastMessageSnippet: originalChat.lastMessageSnippet || '',
                unreadCount: 0
              };
              chatHistory.unshift(newChat);
              saveChatThreads();
              renderChatSidebar();
              if (typeof showToast === 'function') {
                showToast('Chat duplicated successfully', 'success', 2500);
              } else {
                alert('Chat duplicated successfully!');
              }
            }).catch(function (err) {
              console.error('Failed to duplicate conversation', err);
              if (typeof showToast === 'function') {
                showToast('Failed to duplicate chat: ' + err.message, 'error', 3000);
              } else {
                alert('Failed to duplicate chat: ' + err.message);
              }
            }).finally(function () {
              closeAllDropdowns();
            });
          } else {
            // Fallback: purely client-side duplicate
            const newChatId = 'chat_' + Date.now();
            const newChat = {
              id: newChatId,
              title: originalChat.title + ' (Copy)',
              timestamp: new Date().toISOString(),
              active: false
            };

            chatHistory.unshift(newChat);
            saveChatThreads();
            renderChatSidebar(); // Update sidebar with duplicated chat
            if (typeof showToast === 'function') {
              showToast('Chat duplicated successfully', 'success', 2500);
            } else {
              alert('Chat duplicated successfully!');
            }
            closeAllDropdowns();
          }
        }
      }

      // Delete current chat
      function deleteCurrentChat() {
        if (!currentChatMenuId) return;

        if (!confirm('Are you sure you want to delete this chat?')) {
          return;
        }

        const isBackendConversationId = (function () {
          if (typeof currentChatMenuId === 'number' && Number.isInteger(currentChatMenuId)) return true;
          if (typeof currentChatMenuId === 'string' && currentChatMenuId.startsWith('chat_')) return false;
          const n = parseInt(currentChatMenuId, 10);
          return !isNaN(n) && String(n) === String(currentChatMenuId);
        })();

        function applyLocalDeletion() {
          chatHistory = chatHistory.filter(function (chat) { return String(chat.id) !== String(currentChatMenuId); });

          // Remove stored messages for this chat (local cache only)
          try {
            localStorage.removeItem(getChatMessagesKey(currentChatMenuId));
          } catch (e) {
            console.error('Failed to remove chat messages', e);
          }

          saveChatThreads();
          renderChatSidebar(); // Update sidebar after deletion

          if (String(currentChatId) === String(currentChatMenuId)) {
            // If deleting active chat, switch to first available
            if (chatHistory.length > 0) {
              loadChat(chatHistory[0].id);
            } else {
              startNewChat();
            }
          }

          // Remove from UI (backup in case renderChatSidebar didn't catch it)
          const chatElement = document.querySelector(`[onclick="loadChat('${currentChatMenuId}')\"]`);
          if (chatElement) {
            chatElement.remove();
          }
        }

        if (isBackendConversationId) {
          fetch('/delete_conversation/' + currentChatMenuId, {
            method: 'DELETE',
            credentials: 'include'
          }).then(function (response) {
            if (!response.ok) {
              return response.json().catch(function () { return {}; }).then(function (data) {
                throw new Error(data.error || 'Failed to delete conversation');
              });
            }
            return response.json();
          }).then(function () {
            applyLocalDeletion();
            if (typeof showToast === 'function') {
              showToast('Chat deleted successfully', 'success', 2500);
            }
          }).catch(function (err) {
            console.error('Failed to delete conversation', err);
            if (typeof showToast === 'function') {
              showToast('Failed to delete chat: ' + err.message, 'error', 3000);
            } else {
              alert('Failed to delete chat: ' + err.message);
            }
          }).finally(function () {
            closeAllDropdowns();
          });
        } else {
          // Client-only chat
          applyLocalDeletion();
          closeAllDropdowns();
        }
      }

      // Toggle voice input: record with MediaRecorder, then send to backend /api/stt (Whisper)
      async function toggleVoiceInput() {
        const micBtn = document.getElementById('micBtn');
        const voiceStatus = document.getElementById('voiceStatus');
        const voiceText = document.getElementById('voiceText');
        const textarea = document.getElementById('messageInput');

        if (!isListening) {
          try {
            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/mp4';
            mediaRecorder = new MediaRecorder(mediaStream);
            audioChunks = [];
            mediaRecorder.ondataavailable = function (e) {
              if (e.data.size > 0) audioChunks.push(e.data);
            };
            mediaRecorder.onstop = async function () {
              if (mediaStream) {
                mediaStream.getTracks().forEach(function (t) { t.stop(); });
                mediaStream = null;
              }
              if (audioChunks.length === 0) {
                if (voiceStatus) voiceStatus.classList.add('hidden');
                if (micBtn) { micBtn.innerHTML = '<i class="fas fa-microphone"></i>'; micBtn.classList.remove('active'); }
                isListening = false;
                return;
              }
              const blob = new Blob(audioChunks, { type: mimeType });
              if (voiceText) voiceText.textContent = 'Transcribing...';
              try {
                const fd = new FormData();
                fd.append('audio', blob, 'audio.webm');
                const res = await fetch('/api/stt', { method: 'POST', credentials: 'include', body: fd });
                const data = await res.json().catch(function () { return {}; });
                if (res.ok && data.text) {
                  if (textarea) { textarea.value = data.text; updateSendButton(); }
                  if (voiceText) voiceText.textContent = data.text;
                  showToast('Voice transcribed', 'success');
                } else {
                  if (voiceText) voiceText.textContent = data.error || 'Transcription failed';
                  showToast(data.error || 'Transcription failed', 'error');
                }
              } catch (err) {
                console.error('STT error:', err);
                if (voiceText) voiceText.textContent = 'Network error';
                showToast('Could not reach speech service', 'error');
              }
              if (voiceStatus) voiceStatus.classList.add('hidden');
              if (micBtn) { micBtn.innerHTML = '<i class="fas fa-microphone"></i>'; micBtn.classList.remove('active'); }
              isListening = false;
            };
            mediaRecorder.start();
            isListening = true;
            if (voiceStatus) {
              voiceStatus.classList.remove('hidden');
              if (voiceText) voiceText.textContent = 'Listening... Speak now. Click mic again to stop.';
            }
            if (micBtn) { micBtn.innerHTML = '<i class="fas fa-microphone-slash"></i>'; micBtn.classList.add('active'); }
          } catch (err) {
            console.error('Microphone error:', err);
            showToast('Microphone access denied or not available', 'error');
          }
        } else {
          if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
          }
          stopVoiceInput();
        }
      }

      function stopVoiceInput() {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
          mediaRecorder.stop();
        }
        if (mediaStream) {
          mediaStream.getTracks().forEach(function (t) { t.stop(); });
          mediaStream = null;
        }
        if (!mediaRecorder || mediaRecorder.state === 'inactive') {
          isListening = false;
          const voiceStatus = document.getElementById('voiceStatus');
          const micBtn = document.getElementById('micBtn');
          if (voiceStatus) voiceStatus.classList.add('hidden');
          if (micBtn) { micBtn.innerHTML = '<i class="fas fa-microphone"></i>'; micBtn.classList.remove('active'); }
        }
      }

      function stopTextToSpeechSession() {
        if (ttsAudio) {
          try {
            ttsAudio.pause();
            ttsAudio.currentTime = 0;
          } catch (e) {}
          ttsAudio = null;
        }
        if (ttsObjectUrl) {
          try {
            URL.revokeObjectURL(ttsObjectUrl);
          } catch (e) {}
          ttsObjectUrl = null;
        }
        isTextToSpeechActive = false;
        if (window.speechSynthesis) {
          try { window.speechSynthesis.cancel(); } catch (e) {}
        }
        const speakerBtn = document.getElementById('speakerBtn');
        if (speakerBtn) {
          speakerBtn.classList.remove('active');
          speakerBtn.innerHTML = '<i class="fas fa-volume-up"></i>';
        }
        if (activeTtsBtn && activeTtsBtn !== speakerBtn) {
          activeTtsBtn.classList.remove('active');
          activeTtsBtn.innerHTML = '<i class="fas fa-volume-up"></i><span>Listen</span>';
        }
        activeTtsBtn = null;
      }

      document.addEventListener('visibilitychange', function () {
        if (document.hidden) stopTextToSpeechSession();
      });
      window.addEventListener('pagehide', function () {
        stopTextToSpeechSession();
      });

      // Per-message "Listen" button (new_ui card-actions row) — shares the same backend TTS
      // playback session/state as the input-bar speaker button, so starting one stops the other.
      async function playMessageAudio(btn, text) {
        if (!text || !text.trim()) {
          showToast('No text to read aloud', 'info');
          return;
        }
        if (isTextToSpeechActive && activeTtsBtn === btn) {
          stopTextToSpeechSession();
          return;
        }
        stopTextToSpeechSession();
        activeTtsBtn = btn;
        isTextToSpeechActive = true;
        btn.classList.add('active');
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>Loading...</span>';
        try {
          const res = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ text: text })
          });
          if (!res.ok) {
            const err = await res.json().catch(function () { return {}; });
            showToast(err.error || 'TTS failed', 'error');
            stopTextToSpeechSession();
            return;
          }
          const blob = await res.blob();
          if (!isTextToSpeechActive || activeTtsBtn !== btn) return;
          ttsObjectUrl = URL.createObjectURL(blob);
          ttsAudio = new Audio(ttsObjectUrl);
          btn.innerHTML = '<i class="fas fa-volume-up"></i><span>Stop</span>';
          ttsAudio.onended = function () { stopTextToSpeechSession(); };
          ttsAudio.onerror = function () { showToast('Playback failed', 'error'); stopTextToSpeechSession(); };
          ttsAudio.play();
        } catch (err) {
          console.error('Message TTS error:', err);
          showToast('Could not reach speech service', 'error');
          stopTextToSpeechSession();
        }
      }

      // Per-message "Copy" button
      async function copyMessageText(btn, text) {
        try {
          await navigator.clipboard.writeText(text || '');
          showToast('Copied to clipboard', 'success', 1500);
        } catch (e) {
          console.error('Copy failed:', e);
          showToast('Copy failed', 'error');
        }
      }

      // Attach a Listen/Copy actions row to every assistant message bubble, regardless of which
      // code path rendered it (welcome message, addAssistantMessage, loadChat, etc.) — avoids
      // having to touch every individual render call site.
      function attachMessageActionsIfMissing(msgEl) {
        if (!msgEl || !msgEl.classList || !msgEl.classList.contains('assistant')) return;
        const contentEl = msgEl.querySelector('.chat-message-content');
        if (!contentEl || contentEl.querySelector('.msg-actions')) return;

        const actions = document.createElement('div');
        actions.className = 'msg-actions';

        const listenBtn = document.createElement('button');
        listenBtn.type = 'button';
        listenBtn.className = 'msg-action-listen';
        listenBtn.innerHTML = '<i class="fas fa-volume-up"></i><span>Listen</span>';
        listenBtn.addEventListener('click', function () {
          const text = (contentEl.innerText || contentEl.textContent || '').replace(/\s+/g, ' ').trim();
          playMessageAudio(listenBtn, text);
        });

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'msg-action-copy';
        copyBtn.innerHTML = '<i class="fas fa-copy"></i><span>Copy</span>';
        copyBtn.addEventListener('click', function () {
          const text = (contentEl.innerText || contentEl.textContent || '').trim();
          copyMessageText(copyBtn, text);
        });

        actions.appendChild(listenBtn);
        actions.appendChild(copyBtn);
        contentEl.appendChild(actions);
      }

      (function watchChatMessagesForActions() {
        function scan(root) {
          if (!root) return;
          if (root.nodeType === 1 && root.classList && root.classList.contains('chat-message')) {
            attachMessageActionsIfMissing(root);
          }
          if (root.querySelectorAll) {
            root.querySelectorAll('.chat-message.assistant').forEach(attachMessageActionsIfMissing);
          }
        }
        function start() {
          const chatMessages = document.getElementById('chatMessages');
          if (!chatMessages) return;
          scan(chatMessages);
          const observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
              m.addedNodes.forEach(function (node) { scan(node); });
            });
          });
          observer.observe(chatMessages, { childList: true, subtree: true });
        }
        if (document.readyState === 'loading') {
          document.addEventListener('DOMContentLoaded', start);
        } else {
          start();
        }
      })();

      // Toggle text-to-speech: use backend /api/tts (gTTS) and play returned audio
      async function toggleTextToSpeech() {
        const speakerBtn = document.getElementById('speakerBtn');

        if (isTextToSpeechActive) {
          stopTextToSpeechSession();
          return;
        }

        const lastAssistantMessage = document.querySelector('.chat-message.assistant:last-child .chat-message-content');
        const text = lastAssistantMessage ? lastAssistantMessage.textContent.replace(/<[^>]*>/g, '').trim() : '';
        if (!text) {
          showToast('No AI message to read aloud', 'info');
          return;
        }

        speakerBtn.classList.add('active');
        speakerBtn.innerHTML = '<i class="fas fa-volume-mute"></i>';
        isTextToSpeechActive = true;

        try {
          const res = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ text: text })
          });
          if (!res.ok) {
            const err = await res.json().catch(function () { return {}; });
            showToast(err.error || 'TTS failed', 'error');
            speakerBtn.classList.remove('active');
            speakerBtn.innerHTML = '<i class="fas fa-volume-up"></i>';
            isTextToSpeechActive = false;
            return;
          }
          const blob = await res.blob();
          if (!isTextToSpeechActive) return;
          if (ttsObjectUrl) {
            try {
              URL.revokeObjectURL(ttsObjectUrl);
            } catch (e) {}
            ttsObjectUrl = null;
          }
          ttsObjectUrl = URL.createObjectURL(blob);
          ttsAudio = new Audio(ttsObjectUrl);
          ttsAudio.onended = function () {
            if (ttsObjectUrl) {
              try {
                URL.revokeObjectURL(ttsObjectUrl);
              } catch (e) {}
              ttsObjectUrl = null;
            }
            ttsAudio = null;
            speakerBtn.classList.remove('active');
            speakerBtn.innerHTML = '<i class="fas fa-volume-up"></i>';
            isTextToSpeechActive = false;
          };
          ttsAudio.onerror = function () {
            if (ttsObjectUrl) {
              try {
                URL.revokeObjectURL(ttsObjectUrl);
              } catch (e) {}
              ttsObjectUrl = null;
            }
            ttsAudio = null;
            speakerBtn.classList.remove('active');
            speakerBtn.innerHTML = '<i class="fas fa-volume-up"></i>';
            isTextToSpeechActive = false;
            showToast('Playback failed', 'error');
          };
          ttsAudio.play();
        } catch (err) {
          console.error('TTS error:', err);
          showToast('Could not reach speech service', 'error');
          speakerBtn.classList.remove('active');
          speakerBtn.innerHTML = '<i class="fas fa-volume-up"></i>';
          isTextToSpeechActive = false;
        }
      }

      // PDF Assistant functions
      // Uses pdf.js (loaded from CDN) to extract text in the browser and export as Markdown
      (function initPdfAssistant() {
        if (window.pdfjsLib) {
          try {
            pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';
          } catch (e) {
            console.warn('Failed to set pdf.js workerSrc', e);
          }
        }

        const input = document.getElementById('pdfFileInput');
        // #pdfFileInput is now the inline Create Lesson input; only bind when the PDF Assistant panel exists.
        if (input && document.getElementById('pdfUploadMessage')) {
          input.addEventListener('change', () => {
            // Clear previous messages
            document.getElementById('pdfUploadMessage').classList.add('hidden');
            document.getElementById('pdfErrorMessage').classList.add('hidden');
            document.getElementById('pdfExtractPreview').classList.add('hidden');
            document.getElementById('downloadMarkdownBtn').classList.add('hidden');
          });
        }
      })();

      async function processPdfFile() {
        const input = document.getElementById('pdfFileInput');
        const file = input && input.files && input.files[0];
        const messageEl = document.getElementById('pdfUploadMessage');
        const errorEl = document.getElementById('pdfErrorMessage');
        const previewEl = document.getElementById('pdfExtractPreview');

        messageEl.classList.add('hidden');
        errorEl.classList.add('hidden');
        previewEl.classList.add('hidden');
        document.getElementById('downloadMarkdownBtn').classList.add('hidden');

        if (!file) {
          errorEl.textContent = 'Please choose a PDF file first.';
          errorEl.classList.remove('hidden');
          return;
        }

        if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
          errorEl.textContent = 'Selected file is not a PDF.';
          errorEl.classList.remove('hidden');
          return;
        }

        try {
          const arrayBuffer = await file.arrayBuffer();

          if (!window.pdfjsLib) {
            errorEl.textContent = 'pdf.js library not loaded.';
            errorEl.classList.remove('hidden');
            return;
          }

          const loadingTask = pdfjsLib.getDocument({ data: arrayBuffer });
          const pdf = await loadingTask.promise;
          let fullText = '';

          for (let i = 1; i <= pdf.numPages; i++) {
            const page = await pdf.getPage(i);
            const content = await page.getTextContent();
            const strings = content.items.map(item => item.str);
            const pageText = strings.join(' ');
            fullText += pageText.trim() + '\n\n';
          }

          // Basic conversion to markdown: preserve paragraphs (double line breaks)
          const markdown = fullText.trim();

          // Save extracted text in a temporary variable on window for download
          window.__pdfAssistant = window.__pdfAssistant || {};
          window.__pdfAssistant.lastExtract = { filename: file.name, markdown };

          // Show success message with filename
          messageEl.textContent = `PDF '${file.name}' uploaded successfully! You can now download or view the extracted text.`;
          messageEl.classList.remove('hidden');

          // Show preview
          previewEl.textContent = markdown.slice(0, 20000); // limit preview size
          previewEl.classList.remove('hidden');

          // Enable download button
          const downloadBtn = document.getElementById('downloadMarkdownBtn');
          downloadBtn.classList.remove('hidden');
        } catch (err) {
          console.error('PDF extraction failed', err);
          errorEl.textContent = 'Failed to extract text from PDF. The file may be encrypted or corrupted.';
          errorEl.classList.remove('hidden');
        }
      }

      function downloadMarkdown() {
        const state = window.__pdfAssistant && window.__pdfAssistant.lastExtract;
        if (!state) return alert('No extracted content available. Please extract first.');

        const baseName = state.filename.replace(/\.pdf$/i, '');
        const filename = baseName + '.md';
        const blob = new Blob([state.markdown], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }

      function openPdfAssistantModal() {
        document.getElementById('pdfAssistantModal').classList.remove('hidden');
      }

      function closePdfAssistantModal() {
        document.getElementById('pdfAssistantModal').classList.add('hidden');
        // clear inputs
        const input = document.getElementById('pdfFileInput');
        if (input) input.value = '';
        const messageEl = document.getElementById('pdfUploadMessage');
        if (messageEl) { messageEl.classList.add('hidden'); messageEl.textContent = ''; }
        const errorEl = document.getElementById('pdfErrorMessage');
        if (errorEl) { errorEl.classList.add('hidden'); errorEl.textContent = ''; }
        const previewEl = document.getElementById('pdfExtractPreview');
        if (previewEl) { previewEl.classList.add('hidden'); previewEl.textContent = ''; }
        const downloadBtn = document.getElementById('downloadMarkdownBtn');
        if (downloadBtn) downloadBtn.classList.add('hidden');

        // Only show chat input area if Chat tab is currently active
        const chatTabBtn = document.getElementById('chatTabBtn');
        const chatInputArea = document.getElementById('chatInputArea');

        if (chatTabBtn && chatTabBtn.classList.contains('active-tab') && chatInputArea) {
          chatInputArea.style.display = 'block';

          // Use requestAnimationFrame to ensure browser layout calculations are done
          requestAnimationFrame(() => {
            setTimeout(() => {
              scrollToBottom();
              const input = document.getElementById('messageInput');
              if (input) input.focus();
            }, 10);
          });
        }
      }

      // Show Set Prompt Modal
      function showSetPromptModal() {
        // Close OTHER modals but NOT setPromptModal itself
        try {
          const viewLessonModal = document.getElementById('viewLessonModal');
          const createLessonModal = document.getElementById('createLessonModal');
          const saveTemplateModal = document.getElementById('saveTemplateModal');
          const testPromptModal = document.getElementById('testPromptModal');

          if (viewLessonModal) viewLessonModal.classList.add('hidden');
          if (createLessonModal) createLessonModal.classList.add('hidden');
          if (saveTemplateModal) saveTemplateModal.classList.add('hidden');
          if (testPromptModal) testPromptModal.classList.add('hidden');
        } catch (e) {
          console.error('Error closing other modals:', e);
        }

        // Now show THIS modal
        document.getElementById('setPromptModal').classList.remove('hidden');
      }

      // Close Set Prompt Modal
      function closeSetPromptModal() {
        closeAllModals(); // Close all modals for clean state

        // Only show chat input area if Chat tab is currently active
        const chatTabBtn = document.getElementById('chatTabBtn');
        const chatInputArea = document.getElementById('chatInputArea');

        if (chatTabBtn && chatTabBtn.classList.contains('active-tab') && chatInputArea) {
          chatInputArea.style.display = 'block';

          // Use requestAnimationFrame to ensure browser layout calculations are done
          requestAnimationFrame(() => {
            setTimeout(() => {
              scrollToBottom();
              const input = document.getElementById('messageInput');
              if (input) input.focus();
            }, 10);
          });
        }
      }



      // Enhanced RAG Upload Modal (for the Upload PDF button)
      function showRAGUploadModal() {
        // Create a modal for PDF upload with prompt options
        const modalHtml = `
        <div class="fixed inset-0 bg-black bg-opacity-50 flex items-start md:items-center justify-center z-50 overflow-y-auto py-4">
          <div class="bg-white rounded-xl p-4 md:p-6 w-full max-w-md mx-4 my-auto">
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-lg font-semibold text-gray-900">📚 Upload PDF for RAG</h3>
              <button onclick="this.parentElement.parentElement.parentElement.remove()" 
                      class="text-gray-400 hover:text-gray-600">
                <i class="fas fa-times"></i>
              </button>
            </div>
            
            <div class="mb-4">
              <label class="block text-sm font-medium text-gray-700 mb-2">
                Set context/prompt for this PDF
              </label>
              <textarea id="pdfContext" 
                       class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                       rows="3"
                       placeholder="E.g., 'This is a 10th grade biology textbook focusing on cell structure and functions...'"></textarea>
            </div>
            
            <div class="mb-4">
              <label class="block text-sm font-medium text-gray-700 mb-2">
                Select PDF File <span class="text-xs text-gray-500">(Max 100MB)</span>
              </label>
              <input type="file" id="pdfUpload" accept=".pdf" 
                     class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500">
            </div>
            
            <div class="flex flex-col md:flex-row gap-3">
              <button onclick="processPDFUpload()" 
                      class="flex-1 bg-primary-600 text-white py-2.5 px-4 rounded-lg hover:bg-primary-700 transition-colors font-medium">
                Upload & Process
              </button>
              <button onclick="this.parentElement.parentElement.parentElement.remove()" 
                      class="flex-1 bg-gray-200 text-gray-800 py-2.5 px-4 rounded-lg hover:bg-gray-300 transition-colors font-medium">
                Cancel
              </button>
            </div>
          </div>
        </div>
      `;

        const modalContainer = document.createElement('div');
        modalContainer.innerHTML = modalHtml;
        document.body.appendChild(modalContainer);
      }

      async function processPDFUpload() {
        const pdfInput = document.getElementById('pdfUpload');
        const context = document.getElementById('pdfContext').value.trim();

        if (!pdfInput.files.length) {
          alert('Please select a PDF file.');
          return;
        }

        const file = pdfInput.files[0];
        const fileSizeMB = (file.size / (1024 * 1024)).toFixed(2);

        // Validate file size
        const maxSize = 100 * 1024 * 1024; // 100MB
        if (file.size > maxSize) {
          alert('File size exceeds 100MB limit. Please upload a smaller file.');
          return;
        }

        // Validate file type
        if (!file.type.includes('pdf')) {
          alert('Please upload a PDF file only.');
          return;
        }

        // Show processing in current chat while we create a NEW backend conversation/thread
        showTypingIndicator();

        try {
          const formData = new FormData();
          formData.append('file', file);
          formData.append('create_new_thread', 'true'); // always create a fresh thread/conversation

          const response = await fetch('/api/rag/ingest', {
            method: 'POST',
            body: formData,
            credentials: 'include'
          });

          removeTypingIndicator();

          if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            addAssistantMessage('Error: ' + (err.error || 'PDF ingest failed'));
            return;
          }

          const data = await response.json().catch(() => ({}));
          let threadId = data.thread_id || null;
          let conversationId = data.conversation_id != null ? data.conversation_id : null;

          // Persist new thread/conversation ids
          if (threadId) {
            window.currentRAGThreadId = threadId;
            try { localStorage.setItem('teacher_currentRAGThreadId', threadId); } catch (e) {}
          }
          if (conversationId != null) {
            window.currentRAGConversationId = conversationId;
            try { localStorage.setItem('teacher_currentRAGConversationId', String(conversationId)); } catch (e) {}
          }
          // Keep sidebar chat naming consistent for PDF chats.
          const inferredLessonName = (file.name || 'New Conversation').replace(/\.pdf$/i, '').trim();
          await setBackendConversationTitle(conversationId, inferredLessonName);

          // Close the upload modal
          const modal = document.querySelector('[onclick="processPDFUpload()"]')?.closest('.fixed');
          if (modal) modal.remove();

          // If we have a backend conversation, switch UI into that conversation and show messages there
          if (conversationId != null && threadId) {
            enableChatTab();
            showChatTab();

            const pages = (data && (data.num_pages || data.pages || data.documents)) || Math.ceil(file.size / 50000);
            currentPDFData = {
              id: 'lesson_' + Date.now(),
              title: inferredLessonName,
              fileName: file.name,
              fileSize: file.size,
              context: context || '',
              subject: '',
              grade: '',
              numPages: pages,
              createdAt: new Date().toISOString(),
              source: 'pdf_upload'
            };
            try { savePDFDataToStorage(); } catch (e) {}

            try {
              await loadChatHistoryFromBackend();
              await loadChatPromise(conversationId);
            } catch (e) {
              console.error('Error switching to new RAG conversation', e);
            }

            // Preamble already shows PDF summary + action bullets; only add upload markers.
            addUserMessage(`📚 Uploading PDF: ${file.name} (${fileSizeMB} MB)`);
            if (context) {
              addUserMessage(`📝 Context: ${context}`);
            }
          } else {
            // Fallback: no conversation id returned – keep behavior local to current chat
            addUserMessage(`📚 Uploading PDF: ${file.name} (${fileSizeMB} MB)`);
            if (context) {
              addUserMessage(`📝 Context: ${context}`);
            }
            addAssistantMessage(buildPDFUploadSuccessMessage({
              fileName: file.name,
              lessonTitle: inferredLessonName,
              lessonContext: context || '',
              fileSize: file.size,
              pages: Math.ceil(file.size / 50000)
            }));
          }
        } catch (e) {
          console.error('RAG upload error', e);
          removeTypingIndicator();
          addAssistantMessage('Error: Failed to upload PDF. Please try again.');
        }
      }

      // Attach file
      function attachFile() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.pdf,.doc,.docx,.txt,.jpg,.png';
        input.onchange = function (e) {
          const file = e.target.files[0];
          if (file) {
            // Add message about attachment - User on LEFT side
            addUserMessage(`📎 Attached file: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`);

            // Simulate processing
            setTimeout(() => {
              // AI response on RIGHT side
              addAssistantMessage(`✅ I've received your ${file.type.split('/')[1].toUpperCase()} file. I can help you analyze or incorporate this into your teaching materials.`);
            }, 800);
          }
        };
        input.click();
      }

      // Save Lesson from Chat (prefer RAG finalized/in-progress lesson over local template so we save actual AI lesson)
      async function saveLessonFromChat() {
        if (window.currentLessonChatAlreadySaved) {
          setSaveLessonButtonsVisible(false);
          showToast('This lesson is already saved in My Lessons.', 'info', 2500);
          return;
        }

        const readiness = await getLessonSaveReadiness();
        window._lessonSaveFlowStep = readiness.step;
        updateLessonSaveFlowStrip(readiness.step);

        if (!readiness.hasDraft) {
          showLessonSaveFlowGuidance('no_content');
          return;
        }

        if (!readiness.lessonFinalized) {
          showLessonSaveFlowGuidance('need_chat_save');
          return;
        }

        let pdfData = currentPDFData;
        let lessonContent = readiness.lastLessonText;

        if (window.currentRAGThreadId) {
          try {
            const res = await fetch('/api/rag/thread/' + encodeURIComponent(window.currentRAGThreadId) + '/finalized-lesson', { method: 'GET', credentials: 'include' });
            const data = await res.json().catch(function() { return {}; });
            if (res.ok && data && data.success && (data.last_lesson_text || '').trim()) {
              lessonContent = (data.last_lesson_text || '').trim();
              pdfData = pdfData || { title: (data.lesson_title || '').trim() || 'Lesson from RAG', subject: 'General', grade: 'General' };
              if ((data.lesson_title || '').trim() && pdfData && !pdfData.title) {
                pdfData.title = (data.lesson_title || '').trim();
              }
            }
          } catch (e) {
            console.warn('Failed to fetch RAG finalized lesson:', e);
          }
        }

        if (!lessonContent) {
          showLessonSaveFlowGuidance('no_content');
          return;
        }

        saveLessonToMyLessons(pdfData, lessonContent);
      }

      // RAG chat: store thread_id and conversation_id from API for PDF context
      try {
        window.currentRAGThreadId = window.currentRAGThreadId || localStorage.getItem('teacher_currentRAGThreadId') || null;
        window.currentRAGConversationId = window.currentRAGConversationId || localStorage.getItem('teacher_currentRAGConversationId') || null;
      } catch (e) {
        window.currentRAGThreadId = window.currentRAGThreadId || null;
        window.currentRAGConversationId = window.currentRAGConversationId || null;
      }

      // Send message - integrated with POST /api/rag/chat (real PDF/RAG backend)
      async function sendMessage() {
        const input = document.getElementById('messageInput');
        const message = input.value.trim();

        if (!message) return;
        if (window.teacherChatRequestInFlight) {
          if (typeof showToast === 'function') showToast('Please wait for the current response.', 'warning', 2000);
          return;
        }

        window.teacherChatRequestInFlight = true;
        updateSendButton();

        // Capture which chat this request belongs to. If the user switches to a
        // different chat (or starts/loads another one) before the response
        // arrives, a late answer must not be rendered into the now-open chat.
        const requestChatId = currentChatId;

        // Auto-generate title from first message if needed
        const chat = chatHistory.find(c => c.id === currentChatId);
        if (chat && chat.title === 'New Conversation') {
          const autoTitle = generateChatTitle(message);
          updateChatTitle(currentChatId, autoTitle);
        }

        // Add user message on LEFT side
        addUserMessage(message);

        // Clear input and reset height
        input.value = '';
        autoResizeTextarea(input);
        updateSendButton();

        // Show typing indicator on RIGHT side
        showTypingIndicator();
        startChatProgressPolling(window.currentRAGThreadId);

        try {
          // Lecture/lesson generation runs multiple retrieval + LLM rounds server-side and can
          // take minutes. Keep this above the backend budget (gunicorn --timeout 300s in
          // Dockerfile) so we never abandon a turn the server is still working on and still
          // holding the per-user lock. Was previously 180000 (180s) - BELOW gunicorn's 300s -
          // so a legitimately slow-but-successful backend run (confirmed live: a real lesson
          // plan request can take 90s+ under normal load, more under contention) could get
          // aborted client-side while the backend was still validly working, discarding
          // completed work and showing a confusing "Request timed out" error instead of the
          // real answer. 310000 leaves gunicorn's own timeout as the first thing to fire, so a
          // genuine backend timeout still returns a real error response instead of racing it.
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 310000);
          const body = {
            message: message,
            thread_id: window.currentRAGThreadId || null,
            conversation_id: window.currentRAGConversationId || (typeof currentChatId === 'number' ? currentChatId : null)
          };
          const response = await fetch('/api/rag/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body),
            signal: controller.signal
          });
          clearTimeout(timeoutId);

          if (!response.ok) {
            const err = await response.json().catch(function() { return {}; });
            // Busy/throttled is not a failed turn - the previous answer is still coming.
            // Surface it as a transient toast and hand the text back so nothing is lost.
            if (response.status === 429) {
              removeLastUserMessage();
              input.value = message;
              autoResizeTextarea(input);
              if (typeof showToast === 'function') {
                showToast(err.error || 'Please wait for the current response.', 'warning', 4000);
              }
              return;
            }
            addAssistantMessage('Error: ' + (err.error || 'Failed to get response from the server.'));
            return;
          }
          const data = await response.json();
          // If the user has since switched away from the chat this request was
          // sent for, don't let a late-arriving answer land in whatever chat is
          // now open, and don't let it overwrite currentRAGThreadId/currentLessonMarkdown
          // (which belong to the now-active chat, not this one).
          const staleChatSwitch = currentChatId !== requestChatId;
          if (data.error) {
            if (!staleChatSwitch) addAssistantMessage('Error: ' + data.error);
            return;
          }
          var aiResponse = data.message || 'No response received.';
          if (staleChatSwitch) {
            // Still record it against the chat it actually belongs to, just not on screen.
            appendMessageToChat(requestChatId, 'assistant', aiResponse);
            return;
          }
          if (data.thread_id) {
            window.currentRAGThreadId = data.thread_id;
            try { localStorage.setItem('teacher_currentRAGThreadId', data.thread_id); } catch (e) {}
          }
          if (data.conversation_id) {
            window.currentRAGConversationId = data.conversation_id;
            try { localStorage.setItem('teacher_currentRAGConversationId', data.conversation_id); } catch (e) {}
          }
          // Keep currentLessonMarkdown in sync with the actual AI lesson (so Save stores the real lesson, not
          // the template). Always sync on any active RAG thread turn - a length/shape heuristic here
          // previously skipped short but legitimate lesson edits (e.g. "I've added that equation"),
          // so Save could persist a stale prior turn instead of the teacher's most recent change.
          if (window.currentRAGThreadId && aiResponse) {
            currentLessonMarkdown = aiResponse;
          }
          var formatted = (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.formatChatResponse)
            ? TeacherChatFormatter.formatChatResponse(aiResponse) : escapeHtml(aiResponse);
          addAssistantMessage(formatted);
          await refreshSaveLessonAvailability();
          if (window._lessonSaveFlowStep === 3) {
            showToast('Ready for My Lessons — click Save (step 3). Chat finalize alone does not finish this.', 'info', 4500);
          } else if (window._lessonSaveFlowStep === 2) {
            updateLessonSaveFlowStrip(2);
          } else if (window._lessonSaveFlowStep === 1 && window.currentRAGThreadId) {
            updateLessonSaveFlowStrip(1);
          }
        } catch (e) {
          if (currentChatId === requestChatId) {
            const isAbort = e && (e.name === 'AbortError' || e.message === 'The user aborted a request.');
            addAssistantMessage(isAbort
              ? 'Error: Request timed out. Please try again.'
              : 'Error: ' + (e.message || 'Network error. Please try again.'));
          }
        } finally {
          window.teacherChatRequestInFlight = false;
          updateSendButton();
          stopChatProgressPolling();
          removeTypingIndicator();
        }
      }

      // Message persistence helpers
      function saveMessagesForChat(chatId, messages) {
        try {
          localStorage.setItem(getChatMessagesKey(chatId), JSON.stringify(messages || []));
        } catch (e) {
          console.error('Failed to save chat messages', e);
        }
      }

      function loadMessagesForChat(chatId) {
        try {
          return JSON.parse(localStorage.getItem(getChatMessagesKey(chatId)) || '[]');
        } catch (e) {
          console.error('Failed to load chat messages', e);
          return [];
        }
      }

      // ════════════════════════════════════════════════════════════════════════════════
      // SPECIFICATION #2: MESSAGE HANDLING & ISOLATION
      // All messages stored ONLY under the active conversation ID
      // Messages NEVER shared, merged, or inferred across different conversation IDs
      // ════════════════════════════════════════════════════════════════════════════════
      function appendMessageToChat(chatId, role, content) {
        // We no longer store per-message history in localStorage – messages are
        // persisted by the backend (ConversationModel via /api/rag/chat).
        // This helper now only updates in-memory sidebar metadata so the UI
        // stays responsive until the next backend refresh.

        const messageObj = { role: role, content: content, timestamp: new Date().toISOString() };

        try {
          const idx = chatHistory.findIndex(c => String(c.id) === String(chatId));
          const plainText = (typeof content === 'string') ? content.replace(/<[^>]*>/g, '') : String(content);

          if (idx !== -1) {
            chatHistory[idx].timestamp = messageObj.timestamp;
            chatHistory[idx].lastMessageSnippet = plainText.slice(0, 120);

            if (role === 'assistant' && currentChatId !== chatId) {
              chatHistory[idx].unreadCount = (chatHistory[idx].unreadCount || 0) + 1;
            } else if (currentChatId === chatId) {
              chatHistory[idx].unreadCount = 0;
            }

            const [chatObj] = chatHistory.splice(idx, 1);
            chatHistory.unshift(chatObj);
          } else {
            const newThread = {
              id: chatId,
              title: 'Conversation',
              timestamp: messageObj.timestamp,
              active: false,
              lastMessageSnippet: plainText.slice(0, 120),
              unreadCount: (role === 'assistant' && currentChatId !== chatId) ? 1 : 0
            };
            chatHistory.unshift(newThread);
          }

          updateChatHistoryUI();
          renderChatSidebar();
        } catch (e) {
          console.error('Failed to update chat thread metadata', e);
        }
      }

      // Add user message on LEFT side
      function addUserMessage(message) {
        const chatMessages = document.getElementById('chatMessages');
        const messageDiv = document.createElement('div');
        messageDiv.className = 'chat-message user animate-fade-in';
        messageDiv.innerHTML = `
        <div class="chat-message-avatar">
          <div class="w-full h-full flex items-center justify-center bg-blue-100 text-blue-700 font-bold">G</div>
        </div>
        <div class="chat-message-content">${escapeHtml(message)}</div>
      `;
        chatMessages.appendChild(messageDiv);
        scrollToBottom();

        // Persist to active chat (scoped by currentChatId - ensures isolation)
        if (!currentChatId) currentChatId = 'chat1';
        appendMessageToChat(currentChatId, 'user', message);
      }

      // Undo the optimistic echo of a message the server refused to accept (e.g. 429 busy),
      // so the text can be handed back to the input instead of stranding a bubble in the thread.
      function removeLastUserMessage() {
        const chatMessages = document.getElementById('chatMessages');
        if (!chatMessages) return;
        const userMessages = chatMessages.querySelectorAll('.chat-message.user');
        if (userMessages.length) {
          userMessages[userMessages.length - 1].remove();
        }
      }

      // Add assistant message on RIGHT side (message = HTML from TeacherChatFormatter: marked + DOMPurify)
      function addAssistantMessage(message) {
        const chatMessages = document.getElementById('chatMessages');
        // Capture BEFORE appending: if the teacher had already scrolled up to read earlier
        // content, don't yank them to the new reply.
        const wasNearBottom = isChatNearBottom();
        // Drop the "AI is thinking..." bubble first so the lecture starts at the viewport
        // top instead of sitting under the spinner (sendMessage also removes it in finally).
        removeTypingIndicator();
        const messageDiv = document.createElement('div');
        messageDiv.className = 'chat-message assistant animate-fade-in';
        var imgUrl = window.TEACHER_CFG.aiIconUrl;
        messageDiv.innerHTML = `
        <div class="chat-message-avatar">
          <img src="${imgUrl}" alt="AI Assistant" onerror="this.onerror=null; this.src=''; this.innerHTML='<i class=\\'fas fa-graduation-cap\\'></i>'; this.classList.add('flex', 'items-center', 'justify-center')">
        </div>
        <div class="chat-message-content markdown-content tex2jax_process">${message}</div>
      `;
        chatMessages.appendChild(messageDiv);
        var contentEl = messageDiv.querySelector('.chat-message-content');
        // Long lectures used to jump to the chat bottom after MathJax, which parked the
        // teacher on Listen/Copy at the END of the lecture so they had to scroll back up
        // to start reading. Pin the START of this reply in view instead. Do it immediately
        // — the message top does not move when equations expand below, and waiting for
        // typesetting would flash the lecture's end first.
        if (wasNearBottom) scrollMessageToTop(messageDiv);
        if (contentEl && typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.processRenderedContent) {
          TeacherChatFormatter.processRenderedContent(contentEl);
        }

        // Persist
        if (!currentChatId) currentChatId = 'chat1';
        appendMessageToChat(currentChatId, 'assistant', message);

        // Auto-read if text-to-speech is active (backend /api/tts)
        if (isTextToSpeechActive) {
          const plain = message.replace(/<[^>]*>/g, '').trim();
          if (plain) {
            fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ text: plain }) })
              .then(function (r) { return r.ok ? r.blob() : null; })
              .then(function (blob) {
                if (!blob || !isTextToSpeechActive) return;
                if (ttsAudio) {
                  try {
                    ttsAudio.pause();
                    ttsAudio.currentTime = 0;
                  } catch (e) {}
                  ttsAudio = null;
                }
                if (ttsObjectUrl) {
                  try {
                    URL.revokeObjectURL(ttsObjectUrl);
                  } catch (e) {}
                  ttsObjectUrl = null;
                }
                ttsObjectUrl = URL.createObjectURL(blob);
                ttsAudio = new Audio(ttsObjectUrl);
                ttsAudio.onended = function () {
                  if (ttsObjectUrl) {
                    try {
                      URL.revokeObjectURL(ttsObjectUrl);
                    } catch (e) {}
                    ttsObjectUrl = null;
                  }
                  ttsAudio = null;
                };
                ttsAudio.onerror = function () {
                  if (ttsObjectUrl) {
                    try {
                      URL.revokeObjectURL(ttsObjectUrl);
                    } catch (e) {}
                    ttsObjectUrl = null;
                  }
                  ttsAudio = null;
                };
                ttsAudio.play();
              })
              .catch(function () {});
          }
        }
      }

      // Generate AI response
      // Extract topic from user message (e.g., "create a lesson about algebra" → "algebra")
      function extractTopicFromMessage(message) {
        const lowerMsg = message.toLowerCase();

        // Match various patterns like "about X", "on X", "for X"
        const patterns = [
          /about\s+(.+?)(?:\s+for|\s+at|\s+level|$)/i,
          /on\s+(.+?)(?:\s+for|\s+at|\s+level|$)/i,
          /for\s+(.+?)(?:\s+for|\s+at|\s+level|$)/i,
          /create\s+a\s+lesson\s+(?:about\s+)?(.+?)$/i,
          /generate\s+(?:a\s+)?lesson\s+(?:about\s+)?(.+?)$/i,
          /make\s+a\s+lesson\s+(?:about\s+)?(.+?)$/i
        ];

        for (let pattern of patterns) {
          const match = message.match(pattern);
          if (match && match[1]) {
            return match[1].trim().replace(/\s+level|for high school|for elementary|for middle|for college/i, '').trim();
          }
        }

        return 'General Topic';
      }

      // Generate lesson content based on topic
      function generateLessonFromTopic(topic, userMessage) {
        const subject = topic.includes('math') ? 'Mathematics' :
          topic.includes('science') ? 'Science' :
            topic.includes('english') ? 'English' :
              topic.includes('history') ? 'History' : 'General';

        const grade = userMessage.includes('high school') ? 'High School' :
          userMessage.includes('middle') ? 'Middle School' :
            userMessage.includes('elementary') ? 'Elementary' :
              userMessage.includes('college') ? 'College' : 'General';

        const lesson = `# ${topic}

## Course Information
- **Subject:** ${subject}
- **Grade Level:** ${grade}
- **Topic:** ${topic}

## Learning Objectives
By the end of this lesson, students will be able to:
1. Understand fundamental concepts of ${topic.toLowerCase()}
2. Apply key principles and theories to practical scenarios
3. Analyze complex problems using frameworks specific to ${topic.toLowerCase()}
4. Evaluate information critically and synthesize new knowledge
5. Create solutions and implementations based on learned concepts

## Introduction
This comprehensive lesson provides an in-depth exploration of ${topic}. The content is structured to build foundational knowledge and progress toward mastery at the ${grade.toLowerCase()} level.

Key areas covered include:
- Foundational concepts and definitions
- Core principles and theories
- Real-world applications and examples
- Critical analysis and problem-solving
- Assessment and evaluation

## Key Concepts

### 1. Foundational Principles
Understanding ${topic} begins with grasping these essential principles:
- Principle A: Foundation and core understanding
- Principle B: Building blocks for advanced concepts
- Principle C: Real-world applications
- Principle D: Interconnections with related topics

### 2. Core Theories & Frameworks
The following theoretical frameworks guide our understanding:
- **Theory 1:** Primary framework for understanding
- **Theory 2:** Secondary supporting framework
- **Theory 3:** Application-based framework
- **Theory 4:** Critical analysis framework

### 3. Practical Applications
Real-world scenarios where ${topic} is applied:
- Application Area 1: Industry/daily life usage
- Application Area 2: Professional context
- Application Area 3: Academic research
- Application Area 4: Emerging technologies

## Detailed Content

### Section 1: Fundamentals
Start by understanding the basics:
1. Define key terminology
2. Explore historical context
3. Identify core components
4. Understand relationships between concepts

### Section 2: Development & Evolution
How has ${topic} evolved:
1. Historical development timeline
2. Major breakthroughs and discoveries
3. Modern advancements
4. Future directions

### Section 3: Analysis & Critical Thinking
Develop deeper understanding through analysis:
1. Compare and contrast viewpoints
2. Evaluate evidence and arguments
3. Synthesize information from multiple sources
4. Apply critical thinking frameworks

## Learning Activities

### Interactive Exercises
1. **Hands-on Activity 1:** Apply concepts to develop understanding
2. **Problem-Solving Challenge:** Work through realistic scenarios
3. **Collaborative Project:** Group-based learning activity
4. **Case Study Analysis:** Real-world application analysis

### Reflection Questions
- What are the most important concepts you learned?
- How can you apply these concepts in your own life?
- What challenges might you encounter when using this knowledge?
- How does this topic connect to other subjects?

## Assessment & Evaluation

### Knowledge Check
- Quiz covering main concepts
- Short answer questions
- Multiple choice assessments
- Concept mapping exercises

### Skills Demonstration
- Project-based assessment
- Presentation of findings
- Problem-solving demonstration
- Peer evaluation activities

### Extended Learning
- Research paper or report
- Creative project
- Teaching others
- Real-world application

## Resources and References

### Primary Resources
- Textbook chapters relevant to ${topic}
- Academic journals and research papers
- Multimedia resources (videos, documentaries)
- Interactive simulations

### Supplementary Materials
- Practice problems and solutions
- Study guides and summaries
- Case studies and examples
- Guest expert materials

## Conclusion

${topic} encompasses a rich body of knowledge with practical applications across many fields. Through this lesson, you have:
- Built a strong foundation in core concepts
- Learned to apply theoretical knowledge
- Developed critical analysis skills
- Explored real-world connections

## Next Steps

To continue your learning journey:
1. Explore advanced topics in ${topic}
2. Engage in project-based learning
3. Connect knowledge to other disciplines
4. Pursue professional applications

---

**Lesson Created:** ${new Date().toLocaleDateString()}
**Topic:** ${topic}
**Grade Level:** ${grade}`;

        return lesson;
      }

      function generateAIResponse(message) {
        // Check if user asked to create a lesson (with or without PDF)
        if (message.toLowerCase().includes('create a lesson') ||
          message.toLowerCase().includes('generate lesson') ||
          message.toLowerCase().includes('make a lesson')) {

          // If we have a PDF lesson, use that
          if (currentLessonMarkdown) {
            return `
            <div style="background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%); color: white; padding: 1rem; border-radius: 0.5rem; margin-bottom: 0.75rem;">
              <p style="margin: 0; font-weight: 600;"><i class="fas fa-book-open" style="margin-right: 0.5rem;"></i>Lesson: ${currentPDFData.title}</p>
            </div>
            <div style="background: white; border: 1px solid #e5e7eb; border-radius: 0.5rem; padding: 1.25rem; margin-bottom: 1rem;">
              ${currentLessonMarkdown.split('\n').map(line => {
              if (line.startsWith('# ')) return `<h2 style="color: #0ea5e9; font-size: 1.3rem; margin: 0.75rem 0 0.5rem 0; font-weight: 700;">${line.replace('# ', '')}</h2>`;
              if (line.startsWith('## ')) return `<h3 style="color: #0284c7; font-size: 1.1rem; margin: 0.75rem 0 0.5rem 0; font-weight: 600;">${line.replace('## ', '')}</h3>`;
              if (line.startsWith('### ')) return `<h4 style="color: #3b82f6; font-size: 0.95rem; margin: 0.5rem 0 0.3rem 0; font-weight: 600;">${line.replace('### ', '')}</h4>`;
              if (line.startsWith('- ')) return `<div style="margin-left: 1.25rem; color: #374151; margin: 0.25rem 0;">• ${line.replace('- ', '')}</div>`;
              if (line.startsWith('  - ')) return `<div style="margin-left: 2.5rem; color: #6b7280; margin: 0.25rem 0; font-size: 0.95rem;">◦ ${line.replace('  - ', '')}</div>`;
              if (line.startsWith('    - ')) return `<div style="margin-left: 3.75rem; color: #9ca3af; font-size: 0.9rem; margin: 0.25rem 0;">▪ ${line.replace('    - ', '')}</div>`;
              if (line.startsWith('- **')) return `<div style="margin-left: 1.25rem; color: #374151; margin: 0.5rem 0; font-weight: 500;">• ${line.replace('- **', '').replace(':**', ':')}</div>`;
              if (line.startsWith('**') && line.endsWith('**')) return `<div style="color: #1f2937; margin: 0.5rem 0; font-weight: 600;">${line}</div>`;
              if (line.trim() === '') return '<div style="height: 0.25rem;"></div>';
              if (line.startsWith('---')) return '<hr style="border: none; border-top: 1px solid #e5e7eb; margin: 0.75rem 0;">';
              return `<div style="color: #374151; margin: 0.25rem 0; line-height: 1.5;">${line}</div>`;
            }).join('')}
            </div>
            <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
              <button onclick="saveLessonToMyLessons()" style="background: #3b82f6; color: white; padding: 0.5rem 1rem; border: none; border-radius: 0.375rem; cursor: pointer; font-weight: 500; font-size: 0.875rem;">
                <i class="fas fa-save" style="margin-right: 0.375rem;"></i>Save
              </button>
              <button onclick="editLessonContent()" style="background: #0ea5e9; color: white; padding: 0.5rem 1rem; border: none; border-radius: 0.375rem; cursor: pointer; font-weight: 500; font-size: 0.875rem;">
                <i class="fas fa-edit" style="margin-right: 0.375rem;"></i>Edit
              </button>
              <button onclick="cancelLesson()" style="background: #f3f4f6; color: #6b7280; padding: 0.5rem 1rem; border: 1px solid #d1d5db; border-radius: 0.375rem; cursor: pointer; font-weight: 500; font-size: 0.875rem;">
                <i class="fas fa-times" style="margin-right: 0.375rem;"></i>Cancel
              </button>
            </div>
          `;
          } else {
            // Generate lesson from text message topic
            const topic = extractTopicFromMessage(message);
            const generatedLesson = generateLessonFromTopic(topic, message);

            // Store in currentLessonMarkdown so it can be saved
            currentLessonMarkdown = generatedLesson;
            currentPDFData = {
              title: topic,
              subject: 'General',
              grade: 'General',
              fileName: `${topic}.txt`
            };

            return `
            <div style="background: linear-gradient(135deg, #3b82f6 0%, #1a56db 100%); color: white; padding: 1rem; border-radius: 0.5rem; margin-bottom: 0.75rem;">
              <p style="margin: 0; font-weight: 600;"><i class="fas fa-sparkles" style="margin-right: 0.5rem;"></i>✨ Generated Lesson: ${topic}</p>
            </div>
            <div style="background: white; border: 1px solid #e5e7eb; border-radius: 0.5rem; padding: 1.25rem; margin-bottom: 1rem;">
              ${generatedLesson.split('\n').map(line => {
              if (line.startsWith('# ')) return `<h2 style="color: #3b82f6; font-size: 1.3rem; margin: 0.75rem 0 0.5rem 0; font-weight: 700;">${line.replace('# ', '')}</h2>`;
              if (line.startsWith('## ')) return `<h3 style="color: #1a56db; font-size: 1.1rem; margin: 0.75rem 0 0.5rem 0; font-weight: 600;">${line.replace('## ', '')}</h3>`;
              if (line.startsWith('### ')) return `<h4 style="color: #1d4ed8; font-size: 0.95rem; margin: 0.5rem 0 0.3rem 0; font-weight: 600;">${line.replace('### ', '')}</h4>`;
              if (line.startsWith('- ')) return `<div style="margin-left: 1.25rem; color: #374151; margin: 0.25rem 0;">• ${line.replace('- ', '')}</div>`;
              if (line.startsWith('  - ')) return `<div style="margin-left: 2.5rem; color: #6b7280; margin: 0.25rem 0; font-size: 0.95rem;">◦ ${line.replace('  - ', '')}</div>`;
              if (line.startsWith('    - ')) return `<div style="margin-left: 3.75rem; color: #9ca3af; font-size: 0.9rem; margin: 0.25rem 0;">▪ ${line.replace('    - ', '')}</div>`;
              if (line.startsWith('- **')) return `<div style="margin-left: 1.25rem; color: #374151; margin: 0.5rem 0; font-weight: 500;">• ${line.replace('- **', '').replace(':**', ':')}</div>`;
              if (line.startsWith('**') && line.endsWith('**')) return `<div style="color: #1f2937; margin: 0.5rem 0; font-weight: 600;">${line}</div>`;
              if (line.trim() === '') return '<div style="height: 0.25rem;"></div>';
              if (line.startsWith('---')) return '<hr style="border: none; border-top: 1px solid #e5e7eb; margin: 0.75rem 0;">';
              return `<div style="color: #374151; margin: 0.25rem 0; line-height: 1.5;">${line}</div>`;
            }).join('')}
            </div>
            <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
              <button onclick="saveLessonToMyLessons()" style="background: #3b82f6; color: white; padding: 0.5rem 1rem; border: none; border-radius: 0.375rem; cursor: pointer; font-weight: 500; font-size: 0.875rem;">
                <i class="fas fa-save" style="margin-right: 0.375rem;"></i>Save
              </button>
              <button onclick="editLessonContent()" style="background: #0ea5e9; color: white; padding: 0.5rem 1rem; border: none; border-radius: 0.375rem; cursor: pointer; font-weight: 500; font-size: 0.875rem;">
                <i class="fas fa-edit" style="margin-right: 0.375rem;"></i>Edit
              </button>
              <button onclick="cancelLesson()" style="background: #f3f4f6; color: #6b7280; padding: 0.5rem 1rem; border: 1px solid #d1d5db; border-radius: 0.375rem; cursor: pointer; font-weight: 500; font-size: 0.875rem;">
                <i class="fas fa-times" style="margin-right: 0.375rem;"></i>Cancel
              </button>
            </div>
          `;
          }
        }

        // Check for other common commands
        if (message.toLowerCase().includes('summarize')) {
          if (currentPDFData) {
            return `<strong>📝 Summary of "${currentPDFData.title}"</strong><br><br>
            This PDF covers key concepts in ${currentPDFData.subject} for ${currentPDFData.grade} grade level.<br><br>
            <strong>Main Topics:</strong><br>
            • Foundational concepts and principles<br>
            • Core theory and frameworks<br>
            • Practical applications and examples<br>
            • Assessment and evaluation methods<br><br>
            Try asking: "create a lesson" to generate a full structured lesson!`;
          }
        }

        if (message.toLowerCase().includes('quiz') || message.toLowerCase().includes('questions')) {
          if (currentPDFData) {
            return `<strong>❓ Quiz from "${currentPDFData.title}"</strong><br><br>
            <strong>Q1: What are the main learning objectives?</strong><br>
            <strong>Q2: How would you apply this in practice?</strong><br>
            <strong>Q3: What are the key takeaways?</strong><br>
            <strong>Q4: How does this concept relate to real-world scenarios?</strong><br>
            <strong>Q5: What challenges might you encounter and how would you solve them?</strong><br><br>
            Type "create a lesson" to get a structured lesson with assessment activities!`;
          }
        }

        // Default responses
        const customPrompt = localStorage.getItem('teacher_custom_prompt');
        const promptTone = localStorage.getItem('teacher_prompt_tone') || 'professional';

        let responses = [
          `I'd approach that by breaking it down into learning objectives. Here's a structured plan...`,
          `That's an excellent teaching topic! Based on educational research, I recommend...`,
          `For that concept, consider these teaching strategies: 1) Start with real-world examples, 2) Use visual aids, 3) Incorporate hands-on activities...`,
          `Here are some key points to cover: First, establish foundational knowledge. Then, build up to more complex concepts...`,
          `I suggest creating a lesson with these components: Introduction (hook), Direct instruction, Guided practice, Independent work, and Assessment...`
        ];

        if (customPrompt) {
          responses = [
            `Based on your custom instructions, here's my approach: ${customPrompt.substring(0, 100)}...`,
            `Following your teaching style preferences: ${customPrompt.substring(0, 80)}...`,
            `As per your request, I'll focus on practical applications. Here's what I recommend...`
          ];
        }

        const randomResponse = responses[Math.floor(Math.random() * responses.length)];
        return `<h4 class="font-display font-semibold text-gray-900 mb-2">💡 Teaching Recommendation</h4>
             <p class="text-gray-700 mb-3">${randomResponse}</p>
             <div class="p-3 bg-gray-50 rounded-lg">
               <p class="text-sm text-gray-600"><i class="fas fa-lightbulb mr-2"></i>Would you like me to elaborate on any of these points or create specific materials?</p>
             </div>`;
      }

      // Save lesson to My Lessons (saves to database via API)
      function getCurrentChatThreadTitleForLesson() {
        const fallback = 'Lesson ' + new Date().toLocaleDateString();
        const currentId = String(currentChatId || '').trim();
        if (!currentId || !Array.isArray(chatHistory)) return fallback;
        const chat = chatHistory.find(function (c) { return String(c.id) === currentId; });
        if (!chat) return fallback;
        const raw = String(chat.title || '').trim();
        if (!raw) return fallback;
        // Keep saved lesson titles aligned to the actual thread title.
        return raw.replace(/^chat\s*:/i, '').trim() || fallback;
      }

      function buildLessonSaveSignature(baseTitle, lessonContent) {
        const threadPart = String(window.currentRAGThreadId || currentChatId || 'no-thread').trim();
        const titlePart = String(baseTitle || '').trim().toLowerCase();
        const contentPart = String(lessonContent || '').replace(/\s+/g, ' ').trim();
        return threadPart + '::' + titlePart + '::' + contentPart;
      }

      function getUniqueLessonTitle(baseTitle) {
        const cleanedBase = String(baseTitle || '').trim() || ('Lesson ' + new Date().toLocaleDateString());
        const usedTitles = getUsedLessonTitles();
        const usedLookup = new Set(usedTitles.map(function (t) { return String(t || '').trim().toLowerCase(); }));

        if (!usedLookup.has(cleanedBase.toLowerCase())) {
          return cleanedBase;
        }

        let counter = 1;
        let candidate = cleanedBase + ' - Lesson Saved';
        while (usedLookup.has(candidate.toLowerCase())) {
          counter += 1;
          candidate = cleanedBase + ' - Lesson Saved ' + counter;
        }
        return candidate;
      }

      async function saveLessonToMyLessons(pdfDataOverride, lessonContentOverride) {
        const pdfData = pdfDataOverride || currentPDFData;
        const lessonContent = lessonContentOverride || currentLessonMarkdown;

        if (!lessonContent) {
          showToast('No lesson content to save. Please create a lesson first.', 'warning', 3000);
          return;
        }

        // Prefer the title the teacher actually entered (PDF upload modal, or the
        // RAG finalized-lesson's own title) over the auto-generated chat-sidebar
        // title - previously this was ignored entirely, so "Save Lesson" could
        // name the lesson after an unrelated, truncated first-chat-message string
        // (or literally "New Conversation") instead of the intended title.
        const explicitTitle = (pdfData && typeof pdfData.title === 'string') ? pdfData.title.trim() : '';
        const baseTitle = explicitTitle || getCurrentChatThreadTitleForLesson();
        const lessonSignature = buildLessonSaveSignature(baseTitle, lessonContent);
        const savedSignatures = getSavedLessonSignatures();
        if (savedSignatures.includes(lessonSignature)) {
          showToast("This lesson has already been saved. If you'd like to save a new lesson, please create a new lesson and save it.", 'warning', 4500);
          return;
        }

        const finalTitle = getUniqueLessonTitle(baseTitle);
        const focusArea = (pdfData && pdfData.subject) ? String(pdfData.subject) : (pdfData && pdfData.focus_area) || 'General';
        const gradeLevel = (
          (pdfData && pdfData.grade) ? String(pdfData.grade).trim()
            : (pdfData && pdfData.gradeLevel) ? String(pdfData.gradeLevel).trim()
              : ''
        );
        if (!gradeLevel) {
          showToast('Grade is required.', 'error', 3500);
          return;
        }
        const summary = (pdfData && pdfData.summary) ? String(pdfData.summary) : 'Saved from chat.';

        showToast('Saving lesson to My Lessons...', 'info', 2000);

        try {
          const response = await fetch('/api/lessons/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
              title: finalTitle,
              content: lessonContent,
              focus_area: focusArea,
              grade_level: gradeLevel,
              summary: summary,
              // Preserve PDF retrieval linkage for student Ask Question flow.
              // lesson_routes.create_lesson_simple accepts either rag_thread_id or thread_id.
              rag_thread_id: window.currentRAGThreadId || null,
              thread_id: window.currentRAGThreadId || null,
              // Store the active conversation so the lesson view can load the conversation summary.
              conversation_id: window.currentRAGConversationId || null
            })
          });

          const data = await response.json().catch(function() { return {}; });

          if (response.ok && data && data.success) {
            savedSignatures.push(lessonSignature);
            persistSavedLessonSignatures(savedSignatures);
            const usedTitles = getUsedLessonTitles();
            usedTitles.push(finalTitle);
            persistUsedLessonTitles(usedTitles);
            try {
              localStorage.setItem(_teacherScopedStorageKey(LESSON_SAVE_META_KEY), JSON.stringify({ chatId: String(currentChatId || ''), signature: lessonSignature }));
            } catch (e) {}

            showToast(`✅ Lesson "${finalTitle}" saved as draft. Assign a class to publish.`, 'success', 3000);

            // Display full lesson with formatting
            const newLessonId = data.lesson && data.lesson.id ? data.lesson.id : data.id;
            const lessonHTML = `
        <div style="background: #f9fafb; padding: 1.5rem; border-radius: 0.75rem; border: 1px solid #e5e7eb;">
          <h3 style="color: #3b82f6; font-size: 1.2rem; margin: 0 0 1rem 0;"><strong>✅ Lesson Created & Saved!</strong></h3>
          
          <div class="markdown-content tex2jax_process" style="background: white; padding: 1.25rem; border-radius: 0.5rem; max-height: 600px; overflow-y: auto; border: 1px solid #e5e7eb; margin-bottom: 1rem; font-size: 0.95rem; line-height: 1.6;">
            ${(typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.formatChatResponse)
          ? TeacherChatFormatter.formatChatResponse(lessonContent || '')
          : escapeHtml(lessonContent || '')}
          </div>
          
          <div style="background: var(--green-muted); padding: 1rem; border-radius: 0.5rem; border-left: 4px solid #3b82f6; margin-bottom: 1rem;">
            <p style="color: var(--primary-color); margin: 0;"><strong>✅ Your lesson "${finalTitle}" is saved as a draft. Assign it to a class to publish (same as quizzes).</strong></p>
          </div>
          
          <div style="display: flex; gap: 0.75rem; flex-wrap: wrap;">
            <button onclick="showMyLessonsPage()" 
                    style="background: #0ea5e9; color: white; padding: 0.75rem 1.25rem; border: none; border-radius: 0.5rem; cursor: pointer; font-weight: 500; font-size: 0.95rem;">
              <i class="fas fa-book" style="margin-right: 0.5rem;"></i>View My Lessons
            </button>
            <button onclick="showCreateLessonWizard()" 
                    style="background: #f3f4f6; color: #1f2937; padding: 0.75rem 1.25rem; border: 1px solid #d1d5db; border-radius: 0.5rem; cursor: pointer; font-weight: 500; font-size: 0.95rem;">
              <i class="fas fa-plus" style="margin-right: 0.5rem;"></i>Create Another
            </button>
          </div>
        </div>
      `;

            addAssistantMessage(lessonHTML);

            // Clear the current PDF data (both memory and storage)
            currentPDFData = null;
            currentLessonMarkdown = null;
            clearPDFDataFromStorage();
            setSaveLessonButtonsVisible(false);
            window._lessonSaveFlowStep = 1;
            updateLessonSaveFlowStrip(1);
            // Open assign-to-class publish (quiz-parity) for the new lesson.
            setTimeout(function () {
              if (typeof showMyLessonsPage === 'function') showMyLessonsPage();
              if (newLessonId && typeof openLessonAssignPublish === 'function') {
                setTimeout(function () {
                  openLessonAssignPublish(newLessonId, finalTitle);
                }, 350);
              }
            }, 800);
          } else {
            showToast(data.error || 'Failed to save lesson to database.', 'error', 4000);
          }
        } catch (e) {
          console.error('Save lesson error:', e);
          showToast('Failed to save lesson. Please try again.', 'error', 4000);
        }
      }

      // Edit lesson content (placeholder)
      function editLessonContent() {
        addAssistantMessage(`✏️ <strong>Edit Mode</strong><br><br>
        You can edit the lesson content. What would you like to change?<br><br>
        • Add more content to a section<br>
        • Modify assessment activities<br>
        • Change the structure or order<br>
        • Add resources or references<br><br>
        Type your edits or say "save changes" when done!`);
      }

      // Cancel lesson (don't save)
      function cancelLesson() {
        currentPDFData = null;
        currentLessonMarkdown = null;
        clearPDFDataFromStorage();
        setSaveLessonButtonsVisible(false);
        addAssistantMessage(`❌ <strong>Lesson Cancelled</strong><br><br>
        The lesson was not saved. You can:<br>
        • Upload another PDF<br>
        • Ask me other questions about teaching<br>
        • Generate a different lesson<br><br>
        What would you like to do next?`);
      }

      // Show typing indicator on RIGHT side
      function showTypingIndicator() {
        const chatMessages = document.getElementById('chatMessages');
        if (!chatMessages) return;

        // Remove any existing indicator to avoid duplicates
        const existing = document.getElementById('typing-indicator');
        if (existing) {
          existing.remove();
        }

        const typingDiv = document.createElement('div');
        typingDiv.id = 'typing-indicator';
        typingDiv.className = 'chat-message assistant animate-fade-in';
        typingDiv.innerHTML = `
        <div class="chat-message-avatar">
          <img src="${window.TEACHER_CFG.aiIconUrl}" alt="AI Assistant">
        </div>
        <div class="chat-message-content">
          <div class="flex items-center gap-2">
            <div class="loading-dots">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <span class="text-surface-600 text-sm" id="typing-indicator-text">AI is thinking...</span>
          </div>
        </div>
      `;
        chatMessages.appendChild(typingDiv);
        scrollToBottom();
      }

      // Remove typing indicator
      function removeTypingIndicator() {
        const typingIndicator = document.getElementById('typing-indicator');
        if (typingIndicator) {
          typingIndicator.remove();
        }
      }

      // Update the "AI is thinking..." label with what the backend is actually doing right now
      // (e.g. "Searching the document...", "Composing your answer...") so the wait feels active
      // instead of a static spinner.
      function updateTypingIndicatorText(message) {
        const el = document.getElementById('typing-indicator-text');
        if (el && message) el.textContent = message;
      }

      let _chatProgressPollTimer = null;

      function startChatProgressPolling(threadId) {
        stopChatProgressPolling();
        if (!threadId) return;
        _chatProgressPollTimer = setInterval(async function () {
          try {
            const res = await fetch('/api/rag/chat-progress/' + encodeURIComponent(threadId), {
              credentials: 'include'
            });
            if (!res.ok) return;
            const data = await res.json();
            if (data && data.message) updateTypingIndicatorText(data.message);
          } catch (e) {
            // Best-effort only - never surface this as a chat error.
          }
        }, 800);
      }

      function stopChatProgressPolling() {
        if (_chatProgressPollTimer) {
          clearInterval(_chatProgressPollTimer);
          _chatProgressPollTimer = null;
        }
      }

      // Quick question buttons
      function quickQuestion(question) {
        document.getElementById('messageInput').value = question;
        autoResizeTextarea(document.getElementById('messageInput'));
        updateSendButton();
        document.getElementById('messageInput').focus();
      }

      function insertCreateLessonPrompt() {
        quickQuestion(`Create a complete lesson draft using the uploaded document as the source of truth. Include:
- Lesson title
- Short summary
- Learning objectives
- Prerequisites/background
- Key concepts
- Step-by-step lesson sections
- 2 teaching activities
- Teacher notes
Keep it grade-appropriate, structured from basic to advanced, and do not invent missing information.`);
      }

      function insertAssessmentPrompt() {
        quickQuestion(`Create assessment material using the uploaded document and current lesson as the source of truth. Include:
- 8 MCQs with 4 options, correct answer, and short explanation
- 5 short-answer questions
- 3 long-answer questions
- Answer key
- Difficulty level for each question: Easy / Medium / Hard
Align everything with the lesson objectives and do not invent missing information.`);
      }

      // True if the chat is scrolled at (or very near) the bottom already — used so an
      // auto-scroll after an async render doesn't yank the view away from content the
      // teacher deliberately scrolled up to read.
      function isChatNearBottom() {
        const threshold = 150;
        const chatArea = document.getElementById('chatArea');
        if (!chatArea) return true;
        return (chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight) <= threshold;
      }

      // Align a message's start with the top of the chat viewport so a newly arrived
      // lecture is readable from the beginning instead of jumping to its last line.
      function scrollMessageToTop(messageEl, behavior) {
        const chatArea = document.getElementById('chatArea');
        if (!chatArea || !messageEl) return;
        const mode = behavior || 'smooth';
        const padding = 12;
        const top = messageEl.getBoundingClientRect().top - chatArea.getBoundingClientRect().top + chatArea.scrollTop - padding;
        try {
          chatArea.scrollTo({ top: Math.max(0, top), behavior: mode });
        } catch (e) {
          chatArea.scrollTop = Math.max(0, top);
        }
      }

      // Scroll to bottom of chat
      function scrollToBottom(behavior) {
        const mode = behavior || 'smooth';
        const chatArea = document.getElementById('chatArea');
        const chatMessages = document.getElementById('chatMessages');

        if (chatArea) {
          try {
            chatArea.scrollTo({ top: chatArea.scrollHeight, behavior: mode });
          } catch (e) {
            chatArea.scrollTop = chatArea.scrollHeight;
          }
        }

        if (chatMessages && chatMessages.parentElement) {
          const container = chatMessages.parentElement;
          try {
            container.scrollTo({ top: container.scrollHeight, behavior: mode });
          } catch (e) {
            container.scrollTop = container.scrollHeight;
          }
        }
      }

      // Escape HTML to prevent XSS
      function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
      }

      // Open lesson generator
      function openLessonGenerator() {
        const topic = prompt('Enter a topic for your lesson plan:', 'Introduction to Algebra');
        if (topic) {
          // Save to lessons
          const newLesson = {
            id: 'lesson_' + Date.now(),
            title: `Lesson: ${topic}`,
            topic: topic,
            subject: 'General',
            grade: 'General',
            createdBy: "Good",
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
            status: 'draft',
            version: 1
          };

          let lessons = JSON.parse(localStorage.getItem('teacher_lessons') || '[]');
          lessons.push(newLesson);
          localStorage.setItem('teacher_lessons', JSON.stringify(lessons));

          quickQuestion(`Create a complete lesson plan about ${topic}`);
        }
        closeAllDropdowns();
      }

      // Download current chat
      function downloadCurrentChat() {
        const chatContent = document.getElementById('chatMessages').innerText;
        const blob = new Blob([chatContent], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `teacher-chat-${new Date().toISOString().split('T')[0]}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        // Show success message
        const successMsg = document.createElement('div');
        successMsg.className = 'chat-message assistant animate-fade-in';
        successMsg.innerHTML = `
        <div class="chat-message-avatar">
          <img src="${window.TEACHER_CFG.aiIconUrl}" alt="AI Assistant">
        </div>
        <div class="chat-message-content">
          <div class="flex items-center gap-2">
            <i class="fas fa-check-circle text-success-500"></i>
            <span class="font-medium text-success-700">Chat downloaded successfully!</span>
          </div>
        </div>
      `;
        document.getElementById('chatMessages').appendChild(successMsg);
        scrollToBottom();
      }

      // Open settings
      function openSettings() {
        addAssistantMessage('⚙️ <strong>Teacher Settings</strong><br><br>' +
          '• <strong>AI Model Preferences</strong> - Customize AI behavior<br>' +
          '• <strong>Voice Settings</strong> - Adjust speech rate and voice<br>' +
          '• <strong>Export Options</strong> - Set default export formats<br>' +
          '• <strong>Privacy Controls</strong> - Manage data retention<br>' +
          '• <strong>Classroom Integration</strong> - Connect to LMS systems<br><br>' +
          '<em>Settings would be implemented in a dedicated settings panel.</em>');
      }

      // Show privacy info
      function showPrivacyInfo() {
        addAssistantMessage('🔒 <strong>Privacy & Security</strong><br><br>' +
          'Your conversations are:<br>' +
          '• <strong>Encrypted end-to-end</strong><br>' +
          '• <strong>Stored locally</strong> on your device<br>' +
          '• <strong>Never shared</strong> with third parties<br>' +
          '• <strong>Automatically deleted</strong> after 30 days (optional)<br><br>' +
          'For enterprise accounts, additional compliance features are available.');
      }

      // Logout
      async function logout() {
        const confirmed = await showInAppConfirm('Are you sure you want to logout?', {
          title: 'Logout',
          confirmLabel: 'Logout',
          cancelLabel: 'Cancel',
          iconClass: 'fas fa-sign-out-alt'
        });
        if (!confirmed) return;

        try {
          // Call backend logout API so server session is cleared
          const response = await fetch('/auth/logout', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'include'
          });

          // If backend returns a redirect URL, prefer it
          if (response.ok) {
            try {
              const data = await response.json();
              if (data && data.redirect_url) {
                window.location.href = data.redirect_url;
                return;
              }
            } catch (e) {
              // Ignore JSON parse errors and fall through
            }
          }
        } catch (e) {
          console.error('Logout API error:', e);
        }

        // Always clear client-side auth/local state and go to login
        try {
          if (window.auth && typeof auth.logout === 'function') {
            // This will clear localStorage and redirect to /auth/login
            auth.logout();
            return;
          }
        } catch (e) {
          console.error('Client auth logout error:', e);
        }

        // Final fallback redirect
        try {
          localStorage.removeItem('teacher_custom_prompt');
          localStorage.removeItem('teacher_prompt_tone');
        } catch (e) {}
        window.location.href = '/auth/login';
      }

      // ===== TOAST NOTIFICATION SYSTEM =====
      function showToast(message, type = 'success', duration = 4000) {
        const container = document.getElementById('toastContainer');
        if (!container) return;

        // Set styling based on type using custom color palette
        const styles = {
          success: 'bg-success-500 text-white',
          error: 'bg-error-500 text-white',
          warning: 'bg-warning-500 text-white',
          info: 'bg-primary-500 text-white'
        };

        const icons = {
          success: 'fas fa-check-circle',
          error: 'fas fa-exclamation-circle',
          warning: 'fas fa-exclamation-triangle',
          info: 'fas fa-info-circle'
        };

        // Create toast element with all classes at once
        const toast = document.createElement('div');
        toast.className = `pointer-events-auto rounded-lg shadow-lg p-4 animate-fade-in flex items-center gap-3 max-w-sm ${styles[type] || styles.success}`;

        toast.innerHTML = `
    <i class="${icons[type] || icons.success}"></i>
    <span>${message}</span>
    <button onclick="this.parentElement.remove()" class="ml-auto opacity-75 hover:opacity-100">
      <i class="fas fa-times"></i>
    </button>
  `;

        container.appendChild(toast);

        // Auto-remove after duration
        setTimeout(() => {
          toast.style.animation = 'fadeOut 0.3s ease-out forwards';
          setTimeout(() => toast.remove(), 300);
        }, duration);
      }

      // ===== CHAT TAB AND FLOATING ACTION BUTTON FUNCTIONS =====

      // Helper function to deactivate all tabs
      // ════════════════════════════════════════════════════════════════════════════════
      // MODAL STATE MANAGER - Close all modals safely
      // ════════════════════════════════════════════════════════════════════════════════
      function closeAllModals() {
        try {
          const viewLessonModal = document.getElementById('viewLessonModal');
          const expandedLessonPreviewModal = document.getElementById('expandedLessonPreviewModal');
          const expandedConversationSummaryModal = document.getElementById('expandedConversationSummaryModal');
          const setPromptModal = document.getElementById('setPromptModal');
          const createLessonModal = document.getElementById('createLessonModal');
          const saveTemplateModal = document.getElementById('saveTemplateModal');
          const testPromptModal = document.getElementById('testPromptModal');
          const ragPromptModal = document.getElementById('ragPromptModal');

          if (viewLessonModal) {
            viewLessonModal.classList.add('hidden');
            viewLessonModal.classList.remove('visible-modal');
          }
          if (expandedLessonPreviewModal) expandedLessonPreviewModal.classList.add('hidden');
          if (expandedConversationSummaryModal) expandedConversationSummaryModal.classList.add('hidden');
          if (setPromptModal) setPromptModal.classList.add('hidden');
          if (createLessonModal) createLessonModal.classList.add('hidden');
          if (saveTemplateModal) saveTemplateModal.classList.add('hidden');
          if (testPromptModal) testPromptModal.classList.add('hidden');
          if (ragPromptModal) {
            ragPromptModal.classList.add('hidden');
            ragPromptModal.style.display = '';
            ragPromptModal.style.visibility = '';
            ragPromptModal.style.opacity = '';
            ragPromptModal.style.pointerEvents = '';
            ragPromptModal.style.zIndex = '';
          }

          window.currentViewLessonId = null;
          document.body.style.overflow = '';
        } catch (e) {
          console.error('Error closing modals:', e);
        }
      }

      // Expose for inline onclick compatibility across environments.
      window.openExpandedLessonPreview = openExpandedLessonPreview;
      window.closeExpandedLessonPreview = closeExpandedLessonPreview;
      window.openExpandedConversationSummary = openExpandedConversationSummary;
      window.closeExpandedConversationSummary = closeExpandedConversationSummary;
      window.toggleMainLessonModalExpand = toggleMainLessonModalExpand;

      function deactivateAllTabs() {
        document.querySelectorAll('#chatTabBtn, #lessonsTabBtn, #promptTabBtn').forEach(function (el) {
          el.classList.remove('active-tab');
        });
      }

      // Show Chat Tab
      function showChatTab() {
        // Check if lesson creation is complete
        if (!lessonCreationCompleted) {
          showToast('Please create a lesson first to access the chat feature.', 'warning', 3000);
          return;
        }

        if (window.tdShowView) tdShowView('tutor');
        // Deactivate all tabs, then activate chat tab
        deactivateAllTabs();
        const chatTabBtn = document.getElementById('chatTabBtn');
        if (chatTabBtn) chatTabBtn.classList.add('active-tab');

        // Show chat area, input area, and floating button
        const chatInputArea = document.getElementById('chatInputArea');
        const floatingBtn = document.getElementById('floatingActionBtn');
        const chatArea = document.getElementById('chatArea');

        if (chatInputArea) chatInputArea.style.display = 'block';
        if (floatingBtn) floatingBtn.classList.remove('hidden-fab');
        if (chatArea) chatArea.style.display = 'block';
        // Hide floating menu
        document.getElementById('floatingMenu').classList.add('hidden-menu');
        // Hide prompt area if visible
        const promptArea = document.getElementById('promptArea');
        if (promptArea) promptArea.style.display = 'none';

        // If we already have chat history, reopen the last/active conversation instead of wiping it
        if (Array.isArray(chatHistory) && chatHistory.length > 0) {
          // Prefer currentChatId if set, otherwise use most recent by timestamp
          let targetChatId = currentChatId;
          if (!targetChatId) {
            const mostRecentChat = chatHistory.reduce((latest, chat) => {
              return new Date(chat.timestamp) > new Date(latest.timestamp) ? chat : latest;
            });
            targetChatId = mostRecentChat.id;
          }
          loadChat(targetChatId);
        } else {
          // No saved chats yet – show the welcome message
          const chatMessages = document.getElementById('chatMessages');
          chatMessages.innerHTML = `
          <div class="chat-message assistant animate-fade-in">
            <div class="chat-message-avatar">
              <img src="${window.TEACHER_CFG.aiIconUrl}" alt="AI Assistant">
            </div>
            <div class="chat-message-content">
              <h4 class="font-display font-semibold text-surface-900 mb-2">Welcome to Teacher Mode!</h4>
              <p class="text-surface-700">As a teacher, you can:</p>
              <ul class="mt-2 space-y-1 text-surface-600">
                <li class="flex items-start gap-2">
                  <i class="fas fa-check-circle text-success-500 mt-0.5"></i>
                  <span>Create and manage lessons</span>
                </li>
                <li class="flex items-start gap-2">
                  <i class="fas fa-check-circle text-success-500 mt-0.5"></i>
                  <span>Upload PDF materials for RAG</span>
                </li>
                <li class="flex items-start gap-2">
                  <i class="fas fa-check-circle text-success-500 mt-0.5"></i>
                  <span>Generate AI-powered lesson plans</span>
                </li>
                <li class="flex items-start gap-2">
                  <i class="fas fa-check-circle text-success-500 mt-0.5"></i>
                  <span>Chat with AI teaching assistant</span>
                </li>
              </ul>
              <div class="mt-4 p-3 bg-primary-50 rounded-lg border border-primary-100">
                <p class="text-primary-700 text-sm">
                  <i class="fas fa-lightbulb mr-2"></i>
                  <strong>Tip:</strong> Use the floating menu to create lessons, download chat, or reset the conversation.
                </p>
              </div>
            </div>
          </div>
        `;
          scrollToBottom();
        }
      }

      // Enable chat tab after lesson creation
      function enableChatTab() {
        lessonCreationCompleted = true;
        const chatTabBtn = document.getElementById('chatTabBtn');

        if (chatTabBtn) {
          chatTabBtn.classList.remove('disabled-tab');
          chatTabBtn.classList.add('active-tab');
          // Remove the locked badge
          const lockedBadge = chatTabBtn.querySelector('.bg-yellow-100');
          if (lockedBadge) {
            lockedBadge.remove();
          }
          // Change icon color back to primary
          const chatIcon = chatTabBtn.querySelector('.fa-comments');
          if (chatIcon) {
            chatIcon.classList.remove('text-gray-400');
            chatIcon.classList.add('text-primary-500');
          }
        }
        showToast('Chat feature unlocked! You can now chat with the AI assistant.', 'success', 3000);
      }

      // Toggle Floating Menu
      function toggleFloatingMenu() {
        const floatingMenu = document.getElementById('floatingMenu');
        floatingMenu.classList.toggle('hidden-menu');
      }

      // Close floating menu and version dropdowns when clicking outside
      document.addEventListener('click', function (event) {
        const floatingBtn = document.getElementById('floatingActionBtn');
        const floatingMenu = document.getElementById('floatingMenu');

        if (!event.target.closest('.floating-action-button') &&
          !event.target.closest('.floating-menu')) {
          floatingMenu.classList.add('hidden-menu');
        }

        // Close any open version dropdown when clicking outside
        if (!event.target.closest('.version-dropdown-wrapper')) {
          closeAllVersionDropdowns();
        }
      });

      // Simple RAG prompt modal (same behavior as legacy chat.html)
      function showRAGPromptModal() {
        const modal = document.getElementById('ragPromptModal');
        if (!modal) return;
        // Close other large modals so prompt is focused
        try {
          const viewLessonModal = document.getElementById('viewLessonModal');
          const createLessonModal = document.getElementById('createLessonModal');
          if (viewLessonModal) viewLessonModal.classList.add('hidden');
          if (createLessonModal) createLessonModal.classList.add('hidden');
        } catch (e) {
          console.error('Error closing other modals before opening RAG prompt', e);
        }
        // Move modal to end of body to avoid stacking-context issues (like viewLessonModal)
        try {
          document.body.appendChild(modal);
        } catch (e) {}
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        modal.style.visibility = 'visible';
        modal.style.opacity = '1';
        modal.style.pointerEvents = 'auto';
        modal.style.zIndex = '99999';
        if (typeof loadRAGPrompt === 'function') loadRAGPrompt();
        if (typeof updateRagPromptCharCount === 'function') {
          setTimeout(updateRagPromptCharCount, 0);
        }
      }

      function closeRAGPromptModal() {
        const modal = document.getElementById('ragPromptModal');
        if (modal) {
          modal.classList.add('hidden');
          modal.style.display = '';
          modal.style.visibility = '';
          modal.style.opacity = '';
          modal.style.pointerEvents = '';
          modal.style.zIndex = '';
        }
        const wrap = document.getElementById('ragSystemPromptPreviewWrap');
        const errEl = document.getElementById('ragSystemPromptPreviewError');
        const pre = document.getElementById('ragFullSystemPromptReadonly');
        if (wrap) wrap.classList.add('hidden');
        if (errEl) {
          errEl.classList.add('hidden');
          errEl.textContent = '';
        }
        if (pre) pre.textContent = '';
      }

      // Download Chat (uses visible chat DOM first, backend fallback for reopened conversations)
      async function downloadChat() {
        function buildTranscriptFromDOM() {
          const nodes = Array.from(document.querySelectorAll('#chatMessages .chat-message'));
          const lines = [];
          nodes.forEach(function (node) {
            const isUser = node.classList.contains('user');
            const roleLabel = isUser ? 'USER' : 'ASSISTANT';
            const contentEl = node.querySelector('.chat-message-content');
            const rawText = (contentEl ? contentEl.innerText : node.innerText) || '';
            const text = rawText.replace(/\n{3,}/g, '\n\n').trim();
            if (text) lines.push(`${roleLabel}: ${text}`);
          });
          return lines;
        }

        function normalizeRole(role) {
          return role === 'bot' ? 'ASSISTANT' : 'USER';
        }

        let lines = buildTranscriptFromDOM();

        // If the UI is empty (or has only placeholders), fallback to backend conversation data.
        if (lines.length === 0) {
          const numericId = (function () {
            if (typeof currentChatId === 'number' && Number.isInteger(currentChatId)) return currentChatId;
            if (typeof currentChatId === 'string' && currentChatId.startsWith('chat_')) return null;
            const n = parseInt(currentChatId, 10);
            return Number.isNaN(n) ? null : n;
          })();

          if (numericId !== null) {
            try {
              const res = await fetch(`/get_messages/${numericId}`, { method: 'GET', credentials: 'include' });
              if (res.ok) {
                const data = await res.json();
                const messages = (data && data.messages) || [];
                lines = messages
                  .map(function (m) {
                    const content = (m && (m.content || m.message)) ? String(m.content || m.message).trim() : '';
                    if (!content) return null;
                    return `${normalizeRole(m.role)}: ${content}`;
                  })
                  .filter(Boolean);
              }
            } catch (e) {
              console.error('Failed to fetch messages for chat download', e);
            }
          }
        }

        if (lines.length === 0) {
          addAssistantMessage('⚠️ <strong>Download Failed</strong><br><br>No chat messages were found to export.');
          return;
        }

        const text = lines.join('\n\n');
        const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);

        const element = document.createElement('a');
        element.href = url;
        element.download = `chat-${currentChatId || 'chat1'}-${new Date().getTime()}.txt`;
        element.style.display = 'none';

        document.body.appendChild(element);
        element.click();
        document.body.removeChild(element);
        URL.revokeObjectURL(url);

        addAssistantMessage('✅ <strong>Chat Downloaded</strong><br><br>Your conversation has been downloaded as a text file. You can save it for future reference.');
      }

      // Reset Chat
      async function resetChat() {
        if (confirm('Are you sure you want to reset the chat? This will clear all messages.')) {
          // Clear the server-side LangGraph conversation history for this thread
          // first, so a stale checkpoint can never be resumed by the next message.
          // Keeps the uploaded document (vectors/file) intact.
          const threadIdToReset = window.currentRAGThreadId;
          if (threadIdToReset) {
            try {
              await fetch('/api/rag/thread/' + encodeURIComponent(threadIdToReset) + '/reset', {
                method: 'POST',
                credentials: 'include'
              });
            } catch (e) {
              console.error('Failed to reset thread conversation on server', e);
            }
          }

          // Clear messages and show welcome
          const chatMessages = document.getElementById('chatMessages');
          chatMessages.innerHTML = `
          <div class="chat-message assistant animate-fade-in">
            <div class="chat-message-avatar">
              <img src="${window.TEACHER_CFG.aiIconUrl}" alt="AI Assistant">
            </div>
            <div class="chat-message-content">
              <h4 class="font-display font-semibold text-surface-900 mb-2">Chat Reset</h4>
              <p class="text-surface-700">Your conversation has been cleared. Feel free to start a new discussion!</p>
            </div>
          </div>
        `;
          scrollToBottom();

          // Remove stored messages for this chat
          try {
            if (currentChatId) {
              localStorage.removeItem(getChatMessagesKey(currentChatId));
            }
          } catch (e) {
            console.error('Failed to clear stored chat messages', e);
          }

          // Reset sticky RAG thread/conversation identifiers so the next
          // message can't resume the just-cleared conversation via a stale
          // client-side id (mirrors the fix in clearChatContext()).
          window.currentRAGThreadId = null;
          try {
            localStorage.removeItem('teacher_currentRAGThreadId');
          } catch (e) {}

          setSaveLessonButtonsVisible(false);
        }
      }

      // Handle window resize
      window.addEventListener('resize', function () {
        closeAllDropdowns();
      });
      // Add these functions to your existing JavaScript


      const RAG_USER_PROMPT_MAX = 300;

      function countWords(text) {
        if (!text || !String(text).trim()) return 0;
        return String(text).trim().split(/\s+/).filter(Boolean).length;
      }

      function truncateToWordLimit(text, maxWords) {
        const words = String(text || '').trim().split(/\s+/).filter(Boolean);
        if (words.length <= maxWords) return String(text || '').trim();
        return words.slice(0, maxWords).join(' ');
      }

      // Word count (custom prompt textarea; max RAG_USER_PROMPT_MAX words)
      function updateCharCount(textarea) {
        const charCount = document.getElementById('charCount');
        if (charCount) {
          const n = countWords(textarea.value);
          charCount.textContent = n;

          if (n > RAG_USER_PROMPT_MAX) {
            charCount.style.color = '#dc2626';
          } else if (n > Math.floor(RAG_USER_PROMPT_MAX * 0.9)) {
            charCount.style.color = '#f59e0b';
          } else {
            charCount.style.color = '#4b5563';
          }
        }
      }

      // Selected tone for prompt modal
      let selectedTone = 'friendly';

      function selectTone(tone) {
        selectedTone = tone;
        // Update tone-chip styles
        document.querySelectorAll('.tone-chip').forEach(btn => {
          const t = btn.getAttribute('data-tone');
          if (t === tone) {
            btn.classList.remove('bg-gray-100', 'text-gray-700');
            btn.classList.add('bg-primary-600', 'text-white');
          } else {
            btn.classList.remove('bg-primary-600', 'text-white');
            btn.classList.add('bg-gray-100', 'text-gray-700');
          }
        });
      }

      // === Legacy-style global RAG prompt API bindings (match chat.html) ===
      function updateRagPromptCharCount() {
        const textarea = document.getElementById('ragPromptTextarea');
        const el = document.getElementById('ragPromptCharCount');
        if (!textarea || !el) return;
        const n = countWords(textarea.value);
        el.textContent = String(n);
        el.style.color = n > RAG_USER_PROMPT_MAX ? '#dc2626' : '#4b5563';
      }

      async function loadRAGPrompt() {
        const textarea = document.getElementById('ragPromptTextarea');
        try {
          const response = await fetch('/api/rag/prompt', {
            method: 'GET',
            credentials: 'include'
          });
          const data = await response.json().catch(() => ({}));
          if (response.status === 401) {
            showToast(
              'Your session expired. Sign in again, then reopen Set Prompt to load your saved text.',
              'error',
              6000
            );
            return;
          }
          if (!response.ok) {
            const err =
              data.error ||
              (response.status === 403
                ? 'You do not have access to load this prompt.'
                : `The server returned an error (${response.status}). Try again in a moment.`);
            showToast(err, 'error', 5000);
            return;
          }
          if (textarea) {
            textarea.value = data.success && data.prompt != null ? (data.prompt || '') : '';
            if (countWords(textarea.value) > RAG_USER_PROMPT_MAX) {
              textarea.value = truncateToWordLimit(textarea.value, RAG_USER_PROMPT_MAX);
              showToast(
                `Your saved prompt was longer than ${RAG_USER_PROMPT_MAX} words. It was trimmed to the first ${RAG_USER_PROMPT_MAX} words. Edit and save if needed.`,
                'warning',
                7000
              );
            }
            updateRagPromptCharCount();
          }
        } catch (error) {
          console.error('Error loading prompt:', error);
          showToast(
            'Could not reach the server to load your prompt. Check your network connection and try again.',
            'error',
            5000
          );
        }
      }

      async function loadRAGSystemPrompt() {
        const wrap = document.getElementById('ragSystemPromptPreviewWrap');
        const errEl = document.getElementById('ragSystemPromptPreviewError');
        const pre = document.getElementById('ragFullSystemPromptReadonly');
        const noteEl = document.getElementById('ragSystemPromptPreviewNote');
        if (errEl) {
          errEl.classList.add('hidden');
          errEl.textContent = '';
        }
        try {
          const response = await fetch('/api/rag/prompt/preview', {
            method: 'GET',
            credentials: 'include'
          });
          const data = await response.json().catch(() => ({}));
          if (response.status === 401) {
            const msg =
              'You must be signed in to view the full system prompt. Refresh the page and sign in, then try again.';
            if (errEl) {
              errEl.textContent = msg;
              errEl.classList.remove('hidden');
            }
            showToast(msg, 'error', 6000);
            return;
          }
          if (!response.ok || !data.success) {
            const msg =
              data.error ||
              (response.status >= 500
                ? 'The server could not build the preview. Wait a moment and try again.'
                : `Could not load preview (${response.status}).`);
            if (errEl) {
              errEl.textContent = msg;
              errEl.classList.remove('hidden');
            }
            showToast(msg, 'error', 5000);
            return;
          }
          if (pre) pre.textContent = data.full_combined_preview || '';
          if (noteEl && data.note) noteEl.textContent = data.note;
          if (wrap) wrap.classList.remove('hidden');
          showToast('Full system prompt is shown below (read-only).', 'success', 3500);
        } catch (error) {
          console.error('Error loading system prompt preview:', error);
          const msg =
            'Could not load the system prompt preview. Check your network connection and try again.';
          if (errEl) {
            errEl.textContent = msg;
            errEl.classList.remove('hidden');
          }
          showToast(msg, 'error', 5000);
        }
      }

      async function saveRAGPrompt() {
        const textarea = document.getElementById('ragPromptTextarea');
        if (!textarea) {
          showToast('Prompt field is missing from the page. Refresh and try again.', 'error');
          return;
        }
        const prompt = textarea.value.trim();
        if (!prompt) {
          showToast(
            'Enter your custom instructions in the box, or use Delete prompt to clear your custom text.',
            'warning',
            5000
          );
          return;
        }
        const wc = countWords(prompt);
        if (wc > RAG_USER_PROMPT_MAX) {
          showToast(
            `Your text is ${wc} words. Shorten it to ${RAG_USER_PROMPT_MAX} words or fewer, then click Save.`,
            'warning',
            6000
          );
          return;
        }
        try {
          const response = await fetch('/api/rag/prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ prompt: prompt })
          });
          const data = await response.json().catch(() => ({}));
          if (response.ok && data.success) {
            showToast('Your custom prompt was saved. It applies to all your PDF chats.', 'success');
            closeRAGPromptModal();
            return;
          }
          let msg = data.error || 'Save failed.';
          if (data.code === 'PROMPT_TOO_LONG') {
            msg =
              data.error ||
              `Maximum is ${data.max_words || data.max_length || RAG_USER_PROMPT_MAX} words (you have ${data.word_count ?? data.length ?? wc}). Shorten your text and save again.`;
          } else if (data.code === 'PROMPT_REQUIRED') {
            msg = data.error || 'Enter a non-empty prompt or use Delete.';
          } else if (response.status === 401) {
            msg = 'Your session expired. Sign in again, then save your prompt.';
          }
          showToast(msg, 'error', 7000);
        } catch (error) {
          console.error('Error saving prompt:', error);
          showToast(
            'Could not reach the server to save. Check your connection and try again.',
            'error',
            5000
          );
        }
      }

      async function deleteRAGPrompt() {
        if (
          !confirm(
            'Remove your custom prompt from all threads? The app will use only the default PDF instructions until you save a new prompt.'
          )
        ) {
          return;
        }
        try {
          const response = await fetch('/api/rag/prompt', {
            method: 'DELETE',
            credentials: 'include'
          });
          const data = await response.json().catch(() => ({}));
          if (response.status === 401) {
            showToast(
              'Your session expired. Sign in again, then use Delete prompt if you still want to clear your custom text.',
              'error',
              6000
            );
            return;
          }
          if (response.ok && data.success) {
            const textarea = document.getElementById('ragPromptTextarea');
            if (textarea) textarea.value = '';
            updateRagPromptCharCount();
            showToast('Your custom prompt was removed.', 'success');
            return;
          }
          const msg =
            data.error ||
            (response.status >= 500
              ? 'The server could not delete your prompt. Try again in a moment.'
              : `Delete failed (${response.status}).`);
          showToast(msg, 'error', 5000);
        } catch (error) {
          console.error('Error deleting prompt:', error);
          showToast(
            'Could not reach the server. Check your connection and try again.',
            'error',
            5000
          );
        }
      }

      // Generate prompt with AI
      function generatePrompt() {
        const textarea = document.getElementById('customPrompt');
        const currentValue = textarea.value;

        // If empty, provide a template
        if (!currentValue.trim()) {
          const templates = [
            "You are an expert teacher who explains complex topics in simple, engaging ways. Use analogies, real-world examples, and interactive questions to help students understand. Always provide practical applications and encourage critical thinking.",
            "As a creative writing mentor, help users develop compelling stories with rich characters and immersive worlds. Provide constructive feedback, suggest writing exercises, and help overcome writer's block while encouraging original voice and style.",
            "You are a business strategy consultant with decades of experience. Help entrepreneurs analyze markets, develop competitive strategies, and optimize operations. Use frameworks like SWOT analysis, provide actionable advice, and focus on sustainable growth."
          ];

          const randomTemplate = templates[Math.floor(Math.random() * templates.length)];
          textarea.value = randomTemplate;
          updateCharCount(textarea);
          showToast('Generated a sample prompt! Feel free to customize it.', 'success');
        } else {
          // Show suggestion modal
          showToast('Prompt generation feature would use AI to enhance your existing prompt.', 'info');
        }
      }

      // Use template
      function useTemplate(templateType) {
        const textarea = document.getElementById('customPrompt');

        const templates = {
          'stem': "You are a STEM education specialist with expertise in science, technology, engineering, and mathematics. Break down complex concepts into digestible parts using real-world examples, experiments, and interactive questions. Emphasize problem-solving, critical thinking, and practical applications. Always relate topics to current technological advancements and career opportunities.",
          'creative': "You are a creative writing mentor and literary expert. Guide users through character development, world-building, plot structure, and narrative techniques. Provide constructive feedback on writing style, suggest creative exercises, and help overcome writer's block. Encourage originality while teaching storytelling fundamentals across genres like fiction, poetry, and screenwriting.",
          'business': "You are a seasoned business strategist and entrepreneurship coach. Help users analyze markets, develop business plans, create marketing strategies, and optimize operations. Use business frameworks like SWOT analysis, Porter's Five Forces, and Lean Startup methodology. Provide practical, actionable advice while considering risk management, sustainability, and ethical business practices.",
          'language': "You are a language education expert specializing in teaching languages effectively. Focus on conversational practice, grammar fundamentals, vocabulary building, and cultural context. Use immersive techniques, provide pronunciation guidance, and create engaging language exercises. Adapt to different proficiency levels from beginner to advanced, emphasizing practical communication skills."
        };

        if (templates[templateType]) {
          textarea.value = templates[templateType];
          updateCharCount(textarea);
          showToast(`${templateType.charAt(0).toUpperCase() + templateType.slice(1)} template loaded`, 'success');
        }
      }

      // Save template modal
      function showSaveTemplateModal() {
        document.getElementById('saveTemplateModal').classList.remove('hidden');
      }

      function closeSaveTemplateModal() {
        document.getElementById('saveTemplateModal').classList.add('hidden');
        document.getElementById('templateName').value = '';

        // Only show chat input area if Chat tab is currently active
        const chatTabBtn = document.getElementById('chatTabBtn');
        const chatInputArea = document.getElementById('chatInputArea');

        if (chatTabBtn && chatTabBtn.classList.contains('active-tab') && chatInputArea) {
          chatInputArea.style.display = 'block';

          // Use requestAnimationFrame to ensure browser layout calculations are done
          requestAnimationFrame(() => {
            setTimeout(() => {
              scrollToBottom();
              const input = document.getElementById('messageInput');
              if (input) input.focus();
            }, 10);
          });
        }
      }

      function saveTemplate() {
        const name = document.getElementById('templateName').value.trim();
        const category = document.getElementById('templateCategory').value;
        const prompt = document.getElementById('customPrompt').value.trim();

        if (!name) {
          alert('Please enter a template name');
          return;
        }

        if (!prompt) {
          alert('Please create a prompt first');
          return;
        }

        // Save to localStorage
        const template = {
          id: 'template_' + Date.now(),
          name: name,
          category: category,
          prompt: prompt,
          tone: selectedTone,
          createdAt: new Date().toISOString()
        };

        let templates = JSON.parse(localStorage.getItem('teacher_prompt_templates') || '[]');
        templates.push(template);
        localStorage.setItem('teacher_prompt_templates', JSON.stringify(templates));

        closeSaveTemplateModal();
        loadSavedTemplates();
        showToast(`Template "${name}" saved successfully!`, 'success');
      }

      function loadSavedTemplates() {
        const container = document.getElementById('savedTemplates');
        if (!container) return;

        const templates = JSON.parse(localStorage.getItem('teacher_prompt_templates') || '[]');

        if (templates.length === 0) {
          container.innerHTML = `
      <div class="text-center py-4">
        <i class="fas fa-folder-open text-3xl text-gray-300 mb-2"></i>
        <p class="text-sm text-gray-500">No saved templates yet</p>
      </div>
    `;
          return;
        }

        container.innerHTML = templates.map(template => `
    <div class="template-item bg-gray-50 rounded-lg p-3 hover:bg-gray-100 transition-colors cursor-pointer group">
      <div class="flex items-center justify-between">
        <div class="flex-1">
          <div class="flex items-center gap-2 mb-1">
            <h5 class="font-medium text-gray-900 truncate">${template.name}</h5>
            <span class="text-xs px-2 py-0.5 bg-gray-200 text-gray-700 rounded-full">${template.category}</span>
          </div>
          <p class="text-xs text-gray-600 truncate">${template.prompt.substring(0, 80)}...</p>
        </div>
        <div class="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button onclick="loadTemplate('${template.id}')" class="w-8 h-8 rounded-full bg-blue-100 text-blue-600 hover:bg-blue-200 flex items-center justify-center" title="Load">
            <i class="fas fa-upload text-sm"></i>
          </button>
          <button onclick="deleteTemplate('${template.id}')" class="w-8 h-8 rounded-full bg-red-100 text-red-600 hover:bg-red-200 flex items-center justify-center" title="Delete">
            <i class="fas fa-trash text-sm"></i>
          </button>
        </div>
      </div>
      <div class="text-xs text-gray-500 mt-2">${new Date(template.createdAt).toLocaleDateString()}</div>
    </div>
  `).join('');
      }

      function loadTemplate(templateId) {
        const templates = JSON.parse(localStorage.getItem('teacher_prompt_templates') || '[]');
        const template = templates.find(t => t.id === templateId);

        if (template) {
          document.getElementById('customPrompt').value = template.prompt;
          updateCharCount(document.getElementById('customPrompt'));

          if (template.tone) {
            selectTone(template.tone);
          }

          showToast(`Template "${template.name}" loaded`, 'success');
        }
      }

      function deleteTemplate(templateId) {
        if (confirm('Are you sure you want to delete this template?')) {
          let templates = JSON.parse(localStorage.getItem('teacher_prompt_templates') || '[]');
          templates = templates.filter(t => t.id !== templateId);
          localStorage.setItem('teacher_prompt_templates', JSON.stringify(templates));

          loadSavedTemplates();
          showToast('Template deleted', 'warning');
        }
      }

      // Test prompt functionality
      function testPrompt() {
        const prompt = document.getElementById('customPrompt').value.trim();

        if (!prompt) {
          alert('Please create a prompt first');
          return;
        }

        document.getElementById('testPromptPreview').textContent = prompt;
        document.getElementById('testPromptModal').classList.remove('hidden');
      }

      function closeTestPromptModal() {
        document.getElementById('testPromptModal').classList.add('hidden');

        // Only show chat input area if Chat tab is currently active
        const chatTabBtn = document.getElementById('chatTabBtn');
        const chatInputArea = document.getElementById('chatInputArea');

        if (chatTabBtn && chatTabBtn.classList.contains('active-tab') && chatInputArea) {
          chatInputArea.style.display = 'block';

          // Use requestAnimationFrame to ensure browser layout calculations are done
          requestAnimationFrame(() => {
            setTimeout(() => {
              scrollToBottom();
              const input = document.getElementById('messageInput');
              if (input) input.focus();
            }, 10);
          });
        }
      }

      function runPromptTest() {
        const testMessage = document.getElementById('testMessage').value.trim();
        const prompt = document.getElementById('customPrompt').value.trim();

        if (!testMessage) {
          alert('Please enter a test message');
          return;
        }

        const responseContainer = document.getElementById('testResponse');

        // Clear previous response
        responseContainer.innerHTML = `
    <div class="test-response-message test-response-user">
      <strong>You:</strong> ${testMessage}
    </div>
    <div class="test-response-loading">
      <div class="dot-flashing"></div>
      <div class="dot-flashing"></div>
      <div class="dot-flashing"></div>
      <span>AI is thinking...</span>
    </div>
  `;

        // Simulate AI response after delay
        setTimeout(() => {
          const sampleResponses = [
            `Based on your custom prompt, here's my approach: I'll explain quantum physics using simple analogies. Think of particles like students in a classroom - they can be in multiple places at once until you observe them. The core idea is that at the smallest scales, reality behaves very differently from our everyday experience.`,
            `Following your teaching style, I'd break this down: 1) Start with the double-slit experiment as a visual example, 2) Explain wave-particle duality using water wave analogies, 3) Introduce the uncertainty principle with practical examples, 4) Connect to real-world applications like quantum computing.`,
            `As per your instructions to provide engaging explanations: Quantum physics is like a magic show at the atomic level! Particles can teleport (quantum tunneling), be in two places at once (superposition), and instantly communicate across distances (entanglement). It's the rulebook for how reality works at its smallest scales.`
          ];

          const randomResponse = sampleResponses[Math.floor(Math.random() * sampleResponses.length)];

          responseContainer.innerHTML = `
      <div class="test-response-message test-response-user">
        <strong>You:</strong> ${testMessage}
      </div>
      <div class="test-response-message test-response-ai">
        <strong>AI (with your prompt):</strong> ${randomResponse}
      </div>
      <div class="mt-4 p-3 bg-green-50 rounded-lg border border-green-200">
        <p class="text-sm text-green-800">
          <i class="fas fa-check-circle mr-2"></i>
          This response reflects your custom prompt's instructions. The AI is following your specified tone and approach.
        </p>
      </div>
    `;
        }, 1500);
      }

      // Save prompt to backend (POST /api/rag/prompt) and sync localStorage
      async function saveCustomPrompt() {
        const textarea = document.getElementById('customPrompt');
        const prompt = textarea && textarea.value.trim();

        if (!prompt) {
          showToast(
            'Enter your custom instructions in the box, or cancel. Empty prompts cannot be saved.',
            'warning',
            5000
          );
          return;
        }
        const wcSave = countWords(prompt);
        if (wcSave > RAG_USER_PROMPT_MAX) {
          showToast(
            `Your text is ${wcSave} words. Shorten it to ${RAG_USER_PROMPT_MAX} words or fewer, then save.`,
            'warning',
            6000
          );
          return;
        }

        try {
          const response = await fetch('/api/rag/prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ prompt: prompt })
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            let msg = data.error || 'Save failed.';
            if (data.code === 'PROMPT_TOO_LONG') {
              msg =
                data.error ||
                `Maximum is ${data.max_words || data.max_length || RAG_USER_PROMPT_MAX} words (you have ${data.word_count ?? data.length ?? wcSave}). Shorten your text and try again.`;
            } else if (data.code === 'PROMPT_REQUIRED') {
              msg = data.error || 'Enter a non-empty prompt.';
            } else if (response.status === 401) {
              msg = 'Your session expired. Sign in again, then save your prompt.';
            }
            showToast(msg, 'error', 7000);
            return;
          }
          localStorage.setItem('teacher_custom_prompt', prompt);
          localStorage.setItem('teacher_prompt_tone', selectedTone);
          if (textarea) updateCharCount(textarea);
          showToast('Custom prompt saved. It applies to all your PDF chats.', 'success', 3000);
          closeSetPromptModal();
        } catch (e) {
          console.error('Save prompt error:', e);
          showToast(
            'Could not reach the server to save. Check your connection and try again.',
            'error',
            5000
          );
        }
      }

      // Load current prompt from backend (GET /api/rag/prompt); fallback to localStorage
      async function loadCurrentPrompt() {
        const textarea = document.getElementById('customPrompt');
        if (!textarea) return;
        try {
          const response = await fetch('/api/rag/prompt', { method: 'GET', credentials: 'include' });
          const data = await response.json().catch(() => ({}));
          if (response.status === 401) {
            showToast(
              'Your session expired. Sign in again, then use Load current to fetch your saved prompt.',
              'error',
              6000
            );
            return;
          }
          const prompt = (response.ok && data.prompt != null) ? (data.prompt || '') : (localStorage.getItem('teacher_custom_prompt') || '');
          const savedTone = localStorage.getItem('teacher_prompt_tone') || 'professional';
          textarea.value = prompt;
          if (countWords(textarea.value) > RAG_USER_PROMPT_MAX) {
            textarea.value = truncateToWordLimit(textarea.value, RAG_USER_PROMPT_MAX);
            showToast(
              `Your saved prompt was longer than ${RAG_USER_PROMPT_MAX} words; it was trimmed to the first ${RAG_USER_PROMPT_MAX} words. Edit and save.`,
              'warning',
              7000
            );
          }
          selectTone(savedTone);
          updateCharCount(textarea);
          localStorage.setItem('teacher_custom_prompt', textarea.value);
          if (response.ok && data.prompt != null) showToast('Loaded your prompt from the server.', 'info', 3000);
        } catch (e) {
          console.error('Load prompt error:', e);
          const savedPrompt = localStorage.getItem('teacher_custom_prompt') || '';
          const savedTone = localStorage.getItem('teacher_prompt_tone') || 'professional';
          textarea.value = truncateToWordLimit(savedPrompt, RAG_USER_PROMPT_MAX);
          selectTone(savedTone);
          updateCharCount(textarea);
          showToast(
            'Could not reach the server. Showing any text stored in this browser for this prompt.',
            'warning',
            5000
          );
        }
      }

      // Initialize when modal opens
      document.addEventListener('DOMContentLoaded', function () {
        // Load saved templates
        loadSavedTemplates();

        // Initialize word count for custom prompt
        const textarea = document.getElementById('customPrompt');
        if (textarea) {
          updateCharCount(textarea);
          textarea.addEventListener('input', function () {
            updateCharCount(this);
          });
        }

        // Set default tone
        selectTone('friendly');

        // Sync custom prompt from backend so localStorage and RAG use same source
        loadCurrentPrompt();
      });

      // Simple RAG Prompt modal - closeRAGPromptModal defined above with preview cleanup

      // ─── Version Dropdown (Lessons Table) ─────────────────────────────────────
      const _versionDropCache = {};
      // Tracks which version number was last selected per lessonId
      const _versionSelectedMap = {};

      function toggleVersionDropdown(event, lessonId, btn) {
        event.stopPropagation();
        const menu = document.getElementById('vdrop-' + lessonId);
        if (!menu) return;

        // Close every other open version dropdown first
        closeAllVersionDropdowns(menu);

        const isOpen = menu.classList.contains('open');
        if (isOpen) {
          menu.classList.remove('open');
          btn.classList.remove('open');
          return;
        }

        // Position the menu below the button using fixed coords (bypasses overflow:hidden)
        const rect = btn.getBoundingClientRect();
        menu.style.top = (rect.bottom + 4) + 'px';
        const menuWidth = 230;
        const leftPos = Math.min(rect.left, window.innerWidth - menuWidth - 8);
        menu.style.left = leftPos + 'px';

        menu.classList.add('open');
        btn.classList.add('open');

        // Use cached data if available
        if (_versionDropCache[lessonId]) {
          renderVersionDropdownMenu(menu, _versionDropCache[lessonId]);
          return;
        }

        // Show loading placeholder then fetch
        menu.innerHTML = '<div class="version-dropdown-loading"><i class="fas fa-spinner fa-spin"></i> Loading…</div>';

        fetch('/api/lessons/lesson/' + lessonId + '/view', { method: 'GET', credentials: 'include' })
          .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
          .then(function(data) {
            const versions = (data.versions || [])
              .slice()
              .sort(function(a, b) {
                return (b.version_number || b.version || 0) - (a.version_number || a.version || 0);
              });
            const status = (data.lesson && data.lesson.status) || 'draft';
            const payload = { versions: versions, status: status };
            _versionDropCache[lessonId] = payload;
            if (menu.classList.contains('open')) renderVersionDropdownMenu(menu, payload);
          })
          .catch(function() {
            if (menu.classList.contains('open'))
              menu.innerHTML = '<div class="version-dropdown-loading" style="color:#ef4444;"><i class="fas fa-exclamation-circle"></i> Failed to load</div>';
          });
      }

      function renderVersionDropdownMenu(menu, data) {
        const versions = data.versions;
        const status = data.status;
        // Extract lessonId from the menu element id ("vdrop-{lessonId}")
        const lessonId = menu.id.replace('vdrop-', '');
        const selectedVer = _versionSelectedMap[lessonId] || null;

        if (!versions || versions.length === 0) {
          menu.innerHTML = '<div class="version-dropdown-loading">No versions found</div>';
          return;
        }

        const rows = versions.map(function(v, i) {
          const isCurrent = i === 0;
          const vNum = v.version_number || v.version || 1;
          // A version is "selected" if it was previously clicked; otherwise default to the latest
          const isSelected = selectedVer !== null ? (vNum === selectedVer) : isCurrent;
          const dateStr = v.created_at
            ? new Date(v.created_at).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
            : '—';
          const isDraft = isCurrent && status === 'draft';
          let badge = '';
          if (isCurrent && !isDraft) {
            badge = '<span class="v-status-badge current"><i class="fas fa-check"></i> Current</span>';
          } else if (isDraft) {
            badge = '<span class="v-status-badge draft">Draft</span>';
          }
          return '<div class="version-dropdown-item'
            + (isCurrent ? ' is-current' : '')
            + (isSelected ? ' is-selected' : '')
            + '" style="cursor:pointer" title="View v' + vNum + '"'
            + ' onclick="selectVersionFromDropdown(event,\'' + lessonId + '\',' + vNum + ',this)">'
            + '<span class="v-label">v' + vNum + '</span>'
            + '<span class="v-meta">' + dateStr + '</span>'
            + badge
            + '</div>';
        }).join('');

        menu.innerHTML = '<div class="version-dropdown-header"><i class="fas fa-code-branch"></i> Version History</div>' + rows;
      }

      function closeAllVersionDropdowns(except) {
        document.querySelectorAll('.version-dropdown-menu.open').forEach(function(m) {
          if (m === except) return;
          m.classList.remove('open');
          const wrapper = m.closest('.version-dropdown-wrapper');
          if (wrapper) {
            const b = wrapper.querySelector('.version-dropdown-btn');
            if (b) b.classList.remove('open');
          }
        });
      }

      // Called when a user clicks a specific version in the table dropdown.
      // Highlights the selection, updates the badge, then opens the view modal
      // at that version's content.
      async function selectVersionFromDropdown(event, lessonId, versionNumber, itemEl) {
        event.stopPropagation();

        // Record selection
        _versionSelectedMap[lessonId] = versionNumber;

        // Highlight the clicked item; deselect siblings
        const menu = document.getElementById('vdrop-' + lessonId);
        if (menu) {
          menu.querySelectorAll('.version-dropdown-item').forEach(function(el) {
            el.classList.remove('is-selected');
          });
          itemEl.classList.add('is-selected');
        }

        // Update the badge button text to the selected version
        const wrapper = menu ? menu.closest('.version-dropdown-wrapper') : null;
        const btn = wrapper ? wrapper.querySelector('.version-dropdown-btn') : null;
        if (btn) {
          // Determine if this is the latest version
          const cached = _versionDropCache[lessonId];
          const isLatest = cached && cached.versions.length > 0 &&
            (cached.versions[0].version_number || cached.versions[0].version || 1) === versionNumber;
          // Rebuild button content (chevron must be preserved)
          btn.innerHTML = 'v' + versionNumber
            + (isLatest ? '' : ' <span style="font-size:0.65rem;opacity:0.7">(older)</span>')
            + '<i class="fas fa-chevron-down version-chevron"></i>';
          btn.classList.toggle('version-selected', !isLatest);
        }

        // Close the dropdown
        closeAllVersionDropdowns();

        // Open the view modal showing the selected version
        await viewLesson(lessonId, versionNumber);
      }

