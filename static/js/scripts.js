 function toggleDropdown() {
      const dropdown = document.getElementById("dropdownContent");
      dropdown.classList.toggle("hidden");
    }

    function downloadChat(icon) {
      const text = icon.closest(".flex.items-center.justify-between").querySelector("span").innerText;
      const blob = new Blob([text], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "chat.txt";
      a.click();
      URL.revokeObjectURL(url);
    }

    function deleteChat(icon) {
      icon.closest(".flex.items-center.justify-between").remove();
    }

    function hideSidebar() {
      document.getElementById("sidebar").classList.add("hidden");
      document.getElementById("openBtn").classList.remove("hidden");
    }

    function showSidebar() {
      document.getElementById("sidebar").classList.remove("hidden");
      document.getElementById("openBtn").classList.add("hidden");
    }


    function changeclr(btn) {
    // Toggle white background and blue text
    btn.classList.toggle("bg-white");
    btn.classList.toggle("text-blue-500");

    // Optional: also toggle text-gray if needed
    btn.classList.toggle("text-gray-400");
  }
