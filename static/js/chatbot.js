document.addEventListener('DOMContentLoaded', function() {
    // Create chatbot widget elements
    const widget = document.createElement('div');
    widget.className = 'chatbot-widget';
    
    const icon = document.createElement('div');
    icon.className = 'chatbot-icon';
    icon.innerHTML = `<img src="${window.location.origin}/static/images/chatbot-icon.png" alt="Chatbot">`;
    
    const container = document.createElement('div');
    container.className = 'chatbot-container';
    container.innerHTML = `
        <div class="chatbot-header">
            <span>Customer Support</span>
            <span class="close-btn">×</span>
        </div>
        <div class="chatbot-messages" id="chatbot-messages">
            <div class="message bot-message">Hello! How can I help you today?</div>
        </div>
        <div class="chatbot-input">
            <input type="text" id="chatbot-input" placeholder="Type your message...">
            <button id="chatbot-send">Send</button>
        </div>
    `;
    
    widget.appendChild(icon);
    widget.appendChild(container);
    document.body.appendChild(widget);

    // Make the icon draggable
    let isDragging = false;
    let startX, startY;
    let initialRight, initialBottom;

    icon.addEventListener('mousedown', function(e) {
        // Only start drag if clicking on the icon (not the image)
        if (e.target === icon || !icon.contains(e.target)) {
            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;
            
            // Get current position
            const rect = icon.getBoundingClientRect();
            initialRight = window.innerWidth - rect.right;
            initialBottom = window.innerHeight - rect.bottom;
            
            icon.style.cursor = 'grabbing';
            e.preventDefault();
        }
    });

    function handleMove(e) {
        if (!isDragging) return;
        
        // Calculate movement distance
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        
        // Calculate new position
        let newRight = initialRight - dx;
        let newBottom = initialBottom - dy;
        
        // Boundary checks
        newRight = Math.max(0, Math.min(newRight, window.innerWidth - icon.offsetWidth));
        newBottom = Math.max(0, Math.min(newBottom, window.innerHeight - icon.offsetHeight));
        
        // Apply new position
        icon.style.right = newRight + 'px';
        icon.style.bottom = newBottom + 'px';
        
        // Position container relative to icon
        container.style.right = newRight + 'px';
        container.style.bottom = (newBottom + icon.offsetHeight + 10) + 'px';
    }

    function handleMouseUp() {
        if (isDragging) {
            isDragging = false;
            icon.style.cursor = 'grab';
        }
    }

    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleMouseUp);

    // Initialize position (bottom right by default)
    icon.style.position = 'fixed';
    icon.style.right = '20px';
    icon.style.bottom = '20px';
    icon.style.cursor = 'grab';
    icon.style.zIndex = '9999';

    // Position container relative to icon
    container.style.position = 'fixed';
    container.style.right = '20px';
    container.style.bottom = '90px'; // icon height + margin
    container.style.display = 'none';

    // Toggle chat interface
    icon.addEventListener('click', function(e) {
        // Only toggle if not dragging and clicked directly on icon or its image
        if (!isDragging && (e.target === icon || e.target === icon.querySelector('img'))) {
            container.style.display = container.style.display === 'flex' ? 'none' : 'flex';
        }
    });
    
    // Close button
    container.querySelector('.close-btn').addEventListener('click', function() {
        container.style.display = 'none';
    });
    
    // Handle sending messages
    const input = document.getElementById('chatbot-input');
    const sendBtn = document.getElementById('chatbot-send');
    const messagesContainer = document.getElementById('chatbot-messages');
    
    function sendMessage() {
        const message = input.value.trim();
        if (message) {
            // Add user message to chat
            addMessage(message, 'user-message');
            input.value = '';
            
            // Send to backend
            fetch('/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: message })
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.redirect) {
                    // Add bot message with confirmation
                    addMessage(data.message, 'bot-message');
                    
                    // Create a WhatsApp redirect button
                    const redirectDiv = document.createElement('div');
                    redirectDiv.className = 'whatsapp-redirect';
                    
                    const redirectBtn = document.createElement('button');
                    redirectBtn.textContent = 'Open WhatsApp';
                    redirectBtn.className = 'whatsapp-btn';
                    redirectBtn.addEventListener('click', function() {
                        window.open(data.whatsapp_url, '_blank');
                    });
                    
                    redirectDiv.appendChild(redirectBtn);
                    messagesContainer.appendChild(redirectDiv);
                } else {
                    // Normal bot response
                    addMessage(data.message, 'bot-message');
                }
                messagesContainer.scrollTop = messagesContainer.scrollHeight;
            })
            .catch(error => {
                addMessage("Sorry, I'm having trouble connecting. Please try again later.", 'bot-message');
                console.error('Error:', error);
            });
        }
    }

    function addMessage(text, className) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${className}`;
        
        // Format the text while preserving line breaks and bullets
        let formattedText = text
            .replace(/\n- /g, '\n• ')  // Standardize bullet points
            .replace(/^\s*-\s*/gm, '• ')  // Handle dash bullets
            .replace(/^\s*\d+\.\s*/gm, '• ')  // Convert numbered lists
            .replace(/\n/g, '<br>')    // Convert newlines
            .replace(/•/g, '•');     // HTML entity for bullet
        
        messageDiv.innerHTML = formattedText;
        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
    
    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });

    // Cleanup event listeners when needed
    window.addEventListener('beforeunload', function() {
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleMouseUp);
    });
});