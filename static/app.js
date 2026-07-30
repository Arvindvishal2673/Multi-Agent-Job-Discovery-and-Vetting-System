/**
 * AI Job Hunter — Client SPA Logic
 * Features: Drag & Drop file upload, SSE real-time log streaming, live phase stepper,
 * terminal controls, candidate profile view, filterable job cards, and Excel report download.
 */

document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements
    const searchForm = document.getElementById("search-form");
    const dropzone = document.getElementById("dropzone");
    const resumeFileInput = document.getElementById("resume-file");
    const dropzoneContent = document.getElementById("dropzone-content");
    const filePreview = document.getElementById("file-preview");
    const fileNameSpan = document.getElementById("file-name");
    const fileSizeSpan = document.getElementById("file-size");
    const btnRemoveFile = document.getElementById("btn-remove-file");

    const btnStart = document.getElementById("btn-start");

    // Stepper Elements
    const phaseBadge = document.getElementById("current-phase-text");
    const steps = [
        document.getElementById("step-1"),
        document.getElementById("step-2"),
        document.getElementById("step-3"),
        document.getElementById("step-4"),
    ];
    const stepLines = [
        document.getElementById("line-1"),
        document.getElementById("line-2"),
        document.getElementById("line-3"),
    ];

    // Terminal Elements
    const terminalBody = document.getElementById("terminal-body");
    const terminalFilter = document.getElementById("terminal-filter");
    const btnAutoscroll = document.getElementById("btn-autoscroll");
    const btnCopyLogs = document.getElementById("btn-copy-logs");
    const btnClearLogs = document.getElementById("btn-clear-logs");
    const logCountSpan = document.getElementById("log-count");

    // Dashboard Elements
    const tabBtns = document.querySelectorAll(".tab-btn");
    const tabContents = document.querySelectorAll(".tab-content");
    const tabJobCount = document.getElementById("tab-job-count");

    // Metrics
    const mFound = document.getElementById("m-found");
    const mEvaluated = document.getElementById("m-evaluated");
    const mStrong = document.getElementById("m-strong");
    const mTime = document.getElementById("m-time");
    const mSources = document.getElementById("m-sources");
    const mReact = document.getElementById("m-react");

    // Action Banner & Excel Link
    const actionBanner = document.getElementById("action-banner");
    const btnDownloadExcel = document.getElementById("btn-download-excel");

    // Jobs Container & Filters
    const jobsContainer = document.getElementById("jobs-container");
    const filterPills = document.querySelectorAll(".filter-pill");
    const jobSearchInput = document.getElementById("job-search-input");
    const profileContainer = document.getElementById("profile-container");

    // State Variables
    let selectedFile = null;
    let eventSource = null;
    let autoScrollEnabled = true;
    let totalLogLines = 0;
    let allLogs = [];
    let currentJobs = [];
    let currentProfile = null;
    let activeFilter = "all";
    let currentSessionId = null;  // Tracks active session for Tailor Agent calls

    // --- 1. RESUME DROPZONE LOGIC ---
    dropzone.addEventListener("click", () => resumeFileInput.click());
    resumeFileInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) handleFileSelect(e.target.files[0]);
    });

    ["dragenter", "dragover"].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("dragover");
        });
    });

    ["dragleave", "drop"].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("dragover");
        });
    });

    dropzone.addEventListener("drop", (e) => {
        const files = e.dataTransfer.files;
        if (files.length > 0) handleFileSelect(files[0]);
    });

    btnRemoveFile.addEventListener("click", (e) => {
        e.stopPropagation();
        selectedFile = null;
        resumeFileInput.value = "";
        dropzoneContent.classList.remove("hidden");
        filePreview.classList.add("hidden");
    });

    function handleFileSelect(file) {
        selectedFile = file;
        fileNameSpan.textContent = file.name;
        fileSizeSpan.textContent = formatBytes(file.size);
        dropzoneContent.classList.add("hidden");
        filePreview.classList.remove("hidden");
    }

    function formatBytes(bytes) {
        if (bytes === 0) return "0 Bytes";
        const k = 1024;
        const sizes = ["Bytes", "KB", "MB"];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
    }

    // --- 2. FORM SUBMISSION & SSE LOG STREAMING ---
    searchForm.addEventListener("submit", async (e) => {
        e.preventDefault();

        if (!selectedFile) {
            alert("Please upload a resume file (.pdf, .txt, or .md) to start.");
            return;
        }

        // Reset UI State
        resetDashboard();
        setRunningState(true);

        const formData = new FormData();
        formData.append("resume", selectedFile);
        formData.append("locations", document.getElementById("locations").value);
        formData.append("keywords", document.getElementById("keywords").value);
        formData.append("min_salary", document.getElementById("min-salary").value);
        formData.append("max_evals", document.getElementById("max-evals").value);
        formData.append("remote_only", document.getElementById("remote-only").checked);
        formData.append("target_india_only", document.getElementById("target-india-only").checked);
        const postedWithinEl = document.getElementById("posted-within");
        if (postedWithinEl) {
            formData.append("posted_within_days", postedWithinEl.value);
        }

        const apiBase = getApiBase();

        try {
            const res = await fetch(`${apiBase}/api/search`, {
                method: "POST",
                body: formData,
            });

            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail || "Failed to start job search");
            }

            const data = await res.json();
            const sessionId = data.session_id;
            currentSessionId = sessionId;  // Store globally for Tailor Agent
            appendLog(`[SYSTEM] Search session started (ID: ${sessionId.slice(0, 8)})`, "log-system");

            // Connect to SSE log stream
            connectSSE(sessionId);

        } catch (err) {
            let userMsg = err.message;
            if (userMsg.includes("Failed to fetch")) {
                userMsg = "Server not reachable. Please start the server by running 'python server.py' (or 'uvicorn server:app --port 8000') and open http://127.0.0.1:8000 in your browser.";
            }
            appendLog(`[ERROR] Search startup failed: ${userMsg}`, "log-error");
            setRunningState(false);
        }
    });

    function getApiBase() {
        if (window.location.protocol === "file:" || (window.location.port && window.location.port !== "8000")) {
            return "http://127.0.0.1:8000";
        }
        return "";
    }

    function connectSSE(sessionId) {
        if (eventSource) eventSource.close();
        const apiBase = getApiBase();

        eventSource = new EventSource(`${apiBase}/api/stream-logs/${sessionId}`);

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.log) {
                    appendLog(data.log);
                }

                if (data.phase) {
                    updatePhaseStepper(data.phase);
                }

                if (data.status === "completed") {
                    eventSource.close();
                    appendLog("🎉 Pipeline completed successfully! Fetching final results...", "log-system");
                    fetchResults(sessionId);
                } else if (data.status === "failed") {
                    eventSource.close();
                    appendLog(`❌ Execution failed: ${data.error || 'Unknown pipeline error'}`, "log-error");
                    setRunningState(false);
                }

            } catch (err) {
                console.error("Error parsing SSE data:", err);
            }
        };

        eventSource.onerror = (err) => {
            console.error("SSE connection error:", err);
            eventSource.close();
            // Fallback poll check after 2 seconds
            setTimeout(() => fetchResults(sessionId), 2000);
        };
    }

    async function fetchResults(sessionId) {
        const apiBase = getApiBase();
        try {
            const res = await fetch(`${apiBase}/api/results/${sessionId}`);
            if (!res.ok) {
                throw new Error(`Server returned HTTP ${res.status}`);
            }
            const data = await res.json();

            if (data.status === "completed") {
                renderResults(data);
                setRunningState(false);
            } else if (data.status === "failed") {
                appendLog(`❌ Error: ${data.error || 'Pipeline execution failed'}`, "log-error");
                setRunningState(false);
            } else if (data.status === "running") {
                // Server is still processing long operations (e.g. LLM thinking phase)
                // Continue polling every 3 seconds until completed or failed
                setTimeout(() => fetchResults(sessionId), 3000);
            }
        } catch (err) {
            let msg = err.message;
            if (msg.includes("Failed to fetch")) {
                msg = `Connection refused at ${apiBase || "localhost"}. Please verify backend server ('python server.py') is running.`;
            }
            appendLog(`❌ Failed to load final results: ${msg}`, "log-error");
            setRunningState(false);
        }
    }

    // DOM Elements for Stepper Subtexts & Thinking Bar
    const agentThinkingBar = document.getElementById("agent-thinking-bar");
    const thinkingStatusText = document.getElementById("thinking-status-text");

    function setRunningState(running) {
        btnStart.disabled = running;
        if (running) {
            btnStart.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Agent Running...`;
            if (agentThinkingBar) agentThinkingBar.classList.remove("hidden");
        } else {
            btnStart.innerHTML = `Launch AI Job Agent`;
        }
    }

    // --- 3. AI AGENT ACTIVITY STREAM & LOG PARSING ---
    function appendLog(line, forcedClass = "") {
        totalLogLines++;
        allLogs.push(line);
        if (logCountSpan) logCountSpan.textContent = `${totalLogLines} events`;

        // Extract timestamp e.g. [07:48:31]
        const timeMatch = line.match(/\[(\d{2}:\d{2}:\d{2})\]/);
        const timeStr = timeMatch ? timeMatch[1] : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        // Clean technical prefixes
        let cleanText = line.replace(/\[\d{2}:\d{2}:\d{2}\]\s*(INFO|WARNING|ERROR|DEBUG)?\s*\[[^\]]+\]:\s*/, "");

        // Determine Agent Persona, Icon & Avatar Style
        let agentName = "Hunter.ai Orchestrator";
        let icon = "fa-robot";
        let avatarClass = "avatar-system";

        if (line.includes("resume_analyzer") || line.includes("Resume Analysis") || line.includes("ResumeAnalyzer")) {
            agentName = "Resume Analyzer Agent";
            icon = "fa-file-user";
            avatarClass = "avatar-resume";
            if (thinkingStatusText) thinkingStatusText.textContent = "Resume Analyzer parsing candidate skills & profile...";
            updateStepperStep(1, "active", "Parsing Skills");
        } else if (line.includes("search_strategy") || line.includes("Search Strategy") || line.includes("SearchStrategy")) {
            agentName = "Search Strategy Agent";
            icon = "fa-compass";
            avatarClass = "avatar-strategy";
            if (thinkingStatusText) thinkingStatusText.textContent = "AI Strategy Agent generating targeted job queries...";
            updateStepperStep(1, "complete", "Profile Extracted");
            updateStepperStep(2, "active", "Building Strategy");
        } else if (line.includes("planner") || line.includes("PlannerAgent") || line.includes("selected sources")) {
            agentName = "Planner Agent";
            icon = "fa-diagram-project";
            avatarClass = "avatar-planner";
            if (thinkingStatusText) thinkingStatusText.textContent = "Planner Agent selecting optimal job search sources...";
            updateStepperStep(2, "active", "Sources Selected");
        } else if (line.includes("apify") || line.includes("LinkedIn")) {
            agentName = "LinkedIn Searcher (Apify)";
            icon = "fa-linkedin";
            avatarClass = "avatar-ingest";
            if (thinkingStatusText) thinkingStatusText.textContent = "Fetching scraped LinkedIn tech positions...";
            updateStepperStep(2, "complete", "Queries Ready");
            updateStepperStep(3, "active", "Ingesting Boards");
        } else if (line.includes("adzuna") || line.includes("Adzuna")) {
            agentName = "Adzuna Search Agent";
            icon = "fa-briefcase";
            avatarClass = "avatar-ingest";
            if (thinkingStatusText) thinkingStatusText.textContent = "Querying Adzuna REST API for tech jobs...";
            updateStepperStep(2, "complete", "Queries Ready");
            updateStepperStep(3, "active", "Ingesting Boards");
        } else if (line.includes("ats_agent") || line.includes("Direct ATS")) {
            agentName = "Direct ATS Scraper";
            icon = "fa-building-columns";
            avatarClass = "avatar-ingest";
            if (thinkingStatusText) thinkingStatusText.textContent = "Scraping corporate Greenhouse & Lever ATS portals...";
            updateStepperStep(3, "active", "Ingesting Boards");
        } else if (line.includes("reflector") || line.includes("ReAct") || line.includes("Reflection")) {
            agentName = "Quality Evaluator (ReAct)";
            icon = "fa-rotate";
            avatarClass = "avatar-reflect";
            if (thinkingStatusText) thinkingStatusText.textContent = "ReAct Agent evaluating listing relevance & quality...";
            updateStepperStep(3, "active", "ReAct Reflection");
        } else if (line.includes("vetting") || line.includes("Match Evaluator") || line.includes("LLM Vetting")) {
            agentName = "Match Evaluator (Nemotron)";
            icon = "fa-brain";
            avatarClass = "avatar-vetting";
            if (thinkingStatusText) thinkingStatusText.textContent = "NVIDIA Nemotron 120B evaluating candidate fit & score...";
            updateStepperStep(3, "complete", "Listings Ingested");
            updateStepperStep(4, "active", "Nemotron Vetting");
        } else if (line.includes("completed") || line.includes("Finished") || line.includes("Report")) {
            agentName = "Pipeline Coordinator";
            icon = "fa-circle-check";
            avatarClass = "avatar-success";
            if (thinkingStatusText) thinkingStatusText.textContent = "✅ Pipeline finished! All listings vetted and report generated.";
            updateStepperStep(4, "complete", "100% Vetted");
        } else if (line.includes("ERROR") || line.includes("failed") || line.includes("❌")) {
            agentName = "Pipeline Coordinator";
            icon = "fa-triangle-exclamation";
            avatarClass = "avatar-error";
            if (thinkingStatusText) thinkingStatusText.textContent = "❌ Execution error encountered.";
        }

        const item = document.createElement("div");
        item.className = `activity-item ${forcedClass}`;

        item.innerHTML = `
            <div class="item-avatar ${avatarClass}"><i class="fa-solid ${icon}"></i></div>
            <div class="item-content">
                <div class="item-meta">
                    <span class="agent-name">${escapeHtml(agentName)}</span>
                    <span class="item-time">${timeStr}</span>
                </div>
                <div class="item-bubble">${escapeHtml(cleanText)}</div>
            </div>
        `;

        // Apply search filter if active
        const filterText = terminalFilter.value.toLowerCase().trim();
        if (filterText && !cleanText.toLowerCase().includes(filterText) && !agentName.toLowerCase().includes(filterText)) {
            item.classList.add("hidden");
        }

        terminalBody.appendChild(item);

        if (autoScrollEnabled) {
            terminalBody.scrollTop = terminalBody.scrollHeight;
        }
    }

    terminalFilter.addEventListener("input", () => {
        const term = terminalFilter.value.toLowerCase().trim();
        const items = terminalBody.querySelectorAll(".activity-item");
        items.forEach(item => {
            if (!term || item.textContent.toLowerCase().includes(term)) {
                item.classList.remove("hidden");
            } else {
                item.classList.add("hidden");
            }
        });
    });

    btnAutoscroll.addEventListener("click", () => {
        autoScrollEnabled = !autoScrollEnabled;
        btnAutoscroll.classList.toggle("active", autoScrollEnabled);
    });

    btnCopyLogs.addEventListener("click", () => {
        navigator.clipboard.writeText(allLogs.join("\n"));
        alert("Activity Stream copied to clipboard!");
    });

    btnClearLogs.addEventListener("click", () => {
        terminalBody.innerHTML = "";
        totalLogLines = 0;
        allLogs = [];
        if (logCountSpan) logCountSpan.textContent = "0 events";
    });

    // --- 4. DYNAMIC PIPELINE STEPPER ANIMATION ---
    function updatePhaseStepper(phaseText) {
        if (phaseBadge) phaseBadge.textContent = phaseText;

        if (phaseText.includes("Phase 1") && !phaseText.includes("1.5")) {
            updateStepperStep(1, "active", "Parsing Skills");
        } else if (phaseText.includes("Phase 1.5") || phaseText.includes("Phase 2a")) {
            updateStepperStep(1, "complete", "Profile Extracted");
            updateStepperStep(2, "active", "Building Strategy");
        } else if (phaseText.includes("Phase 2") && !phaseText.includes("2b")) {
            updateStepperStep(1, "complete", "Profile Extracted");
            updateStepperStep(2, "complete", "Queries Ready");
            updateStepperStep(3, "active", "Ingesting Boards");
        } else if (phaseText.includes("Phase 2b")) {
            updateStepperStep(3, "active", "ReAct Reflection");
        } else if (phaseText.includes("Phase 3")) {
            updateStepperStep(1, "complete", "Profile Extracted");
            updateStepperStep(2, "complete", "Queries Ready");
            updateStepperStep(3, "complete", "Listings Ingested");
            updateStepperStep(4, "active", "Nemotron Vetting");
        } else if (phaseText.includes("Phase 4") || phaseText.includes("Completed")) {
            updateStepperStep(1, "complete", "Profile Extracted");
            updateStepperStep(2, "complete", "Queries Ready");
            updateStepperStep(3, "complete", "Listings Ingested");
            updateStepperStep(4, "complete", "100% Vetted");
        }
    }

    function updateStepperStep(stepNum, status, text) {
        const step = steps[stepNum - 1];
        const circle = document.getElementById(`circle-${stepNum}`);
        const subtext = document.getElementById(`subtext-${stepNum}`);
        const line = document.getElementById(`line-${stepNum}`);

        if (!step) return;

        const defaultIcons = ["fa-file-user", "fa-compass", "fa-network-wired", "fa-brain"];

        if (status === "active") {
            step.className = "step step-active";
            if (subtext) subtext.textContent = text;
            if (circle) circle.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i>`;
        } else if (status === "complete") {
            step.className = "step step-complete";
            if (subtext) subtext.textContent = text;
            if (circle) circle.innerHTML = `<i class="fa-solid fa-check"></i>`;
            if (line) line.className = "step-line step-line-active";
        } else if (status === "reset") {
            step.className = "step";
            if (subtext) subtext.textContent = "Waiting";
            if (circle) circle.innerHTML = `<i class="fa-solid ${defaultIcons[stepNum - 1]}"></i>`;
            if (line) line.className = "step-line";
        }
    }


    // --- 5. RESULTS RENDERING & DASHBOARD TABS ---
    function renderResults(data) {
        const metrics = data.metrics;
        currentJobs = data.jobs || [];
        currentProfile = data.profile || {};

        // Update metrics
        mFound.textContent = metrics.total_found || 0;
        mEvaluated.textContent = metrics.evaluated || 0;
        mStrong.textContent = metrics.strong_fits || 0;
        mTime.textContent = `${metrics.elapsed_seconds || 0}s`;
        mSources.textContent = (metrics.activated_sources || []).length;
        mReact.textContent = metrics.react_iterations || 0;

        tabJobCount.textContent = currentJobs.length;

        // Download Excel button
        const apiBase = (window.location.protocol === "file:") ? "http://127.0.0.1:8000" : "";
        btnDownloadExcel.href = `${apiBase}/api/download-excel/${data.session_id || ''}`;
        actionBanner.classList.remove("hidden");


        // Render job cards & profile
        renderJobs();
        renderProfile();
    }

    function renderJobs() {
        jobsContainer.innerHTML = "";

        const searchTerm = jobSearchInput.value.toLowerCase().trim();

        const filtered = currentJobs.filter(job => {
            const matchFilter = activeFilter === "all" || job.fit_decision === activeFilter;
            const matchSearch = !searchTerm ||
                job.title.toLowerCase().includes(searchTerm) ||
                (job.company && job.company.toLowerCase().includes(searchTerm)) ||
                (job.location && job.location.toLowerCase().includes(searchTerm));
            return matchFilter && matchSearch;
        });

        if (filtered.length === 0) {
            jobsContainer.innerHTML = `
                <div class="empty-state">
                    <i class="fa-solid fa-folder-open"></i>
                    <p>No job matches found matching current filters.</p>
                </div>`;
            return;
        }

        filtered.forEach((job, idx) => {
            const jobIdx = currentJobs.indexOf(job);
            const card = document.createElement("div");
            card.className = "job-card";

            const fitClass = job.fit_decision === "Strong Fit" ? "fit-strong" :
                job.fit_decision === "Decent Fit" ? "fit-decent" : "fit-weak";
            const fitIcon = job.fit_decision === "Strong Fit" ? "fa-award" :
                job.fit_decision === "Decent Fit" ? "fa-circle-check" : "fa-circle-exclamation";

            const initial = (job.company || job.title || "J").charAt(0).toUpperCase();
            // Random but deterministic avatar color per company
            const avatarColors = ["#2563EB","#7C3AED","#059669","#DC2626","#D97706","#0891B2","#BE185D","#4F46E5"];
            const avatarColor = avatarColors[(job.company || "J").charCodeAt(0) % avatarColors.length];

            const reasonsHtml = (job.fit_reasons || []).map(r =>
                `<li><i class="fa-solid fa-check" style="color:#34D399;flex-shrink:0;margin-top:2px;"></i><span>${escapeHtml(r)}</span></li>`
            ).join("") || `<li><i class="fa-solid fa-check" style="color:#34D399;"></i><span>Matched target qualifications</span></li>`;

            const gapsHtml = (job.gaps_identified || []).map(g =>
                `<li><i class="fa-solid fa-triangle-exclamation" style="color:#FBBF24;flex-shrink:0;margin-top:2px;"></i><span>${escapeHtml(g)}</span></li>`
            ).join("") || `<li><i class="fa-solid fa-shield-check" style="color:#34D399;"></i><span>No significant skill gaps identified!</span></li>`;

            const detailsId = `details-${jobIdx}`;

            card.innerHTML = `
                <div class="job-card-header" style="cursor:pointer;" data-details="${detailsId}">
                    <div class="job-main-info">
                        <div class="company-avatar" style="background:${avatarColor};">${escapeHtml(initial)}</div>
                        <div class="job-titles-group">
                            <h3 class="job-card-title">${idx + 1}. ${escapeHtml(job.title)}</h3>
                            <div class="job-meta-row">
                                <span class="meta-item"><i class="fa-solid fa-building"></i> ${escapeHtml(job.company || 'Unknown')}</span>
                                <span class="meta-item"><i class="fa-solid fa-location-dot"></i> ${escapeHtml(job.location || 'Unknown')}</span>
                                ${job.salary ? `<span class="meta-item"><i class="fa-solid fa-money-bill-wave"></i> ${escapeHtml(job.salary)}</span>` : ''}
                                ${job.posted_at ? `<span class="meta-item" style="color: #38BDF8; font-weight: 500;"><i class="fa-solid fa-clock"></i> ${escapeHtml(job.posted_at)}</span>` : `<span class="meta-item" style="color: #38BDF8; font-weight: 500;"><i class="fa-solid fa-fire"></i> Fresh</span>`}
                            </div>
                        </div>
                    </div>
                    <div class="card-right-group">
                        <span class="fit-badge ${fitClass}">
                            <i class="fa-solid ${fitIcon}"></i>
                            ${Math.round(job.fit_score)}/100 · ${escapeHtml(job.fit_decision)}
                        </span>
                        <button class="btn-expand-details" data-details="${detailsId}" title="Show match reasons & gaps">
                            <i class="fa-solid fa-chevron-down"></i>
                        </button>
                    </div>
                </div>

                <div class="vetting-details vetting-details--collapsed" id="${detailsId}">
                    <div class="details-block">
                        <h4 class="reasons-title"><i class="fa-solid fa-circle-check" style="color:#34D399;"></i> Agent Match Reasons</h4>
                        <ul class="bullet-list reasons-list">${reasonsHtml}</ul>
                    </div>
                    <div class="details-block">
                        <h4 class="gaps-title"><i class="fa-solid fa-circle-exclamation" style="color:#FBBF24;"></i> Skill Gaps</h4>
                        <ul class="bullet-list gaps-list">${gapsHtml}</ul>
                    </div>
                </div>

                <div class="job-card-footer">
                    <span class="source-tag"><i class="fa-solid fa-globe"></i> ${escapeHtml(job.source || 'Web')}</span>
                    <div class="job-card-actions">
                        <button class="btn-tailor" id="btn-tailor-${jobIdx}" title="Generate AI-tailored cover letter, outreach message & ATS pitch for this job">
                            <i class="fa-solid fa-wand-magic-sparkles"></i> Tailor Application
                        </button>
                        <a href="${job.url}" target="_blank" class="btn-apply">
                            Open Listing <i class="fa-solid fa-arrow-up-right-from-square"></i>
                        </a>
                    </div>
                </div>
            `;

            jobsContainer.appendChild(card);

            // Toggle vetting details on header / chevron click
            const detailsPanel = card.querySelector(`#${detailsId}`);
            const expandBtn = card.querySelector(".btn-expand-details");
            const headerRow = card.querySelector(".job-card-header");

            function toggleDetails(e) {
                // Don't trigger if clicking a link inside header
                if (e && e.target.closest("a")) return;
                const isOpen = detailsPanel.classList.contains("vetting-details--open");
                detailsPanel.classList.toggle("vetting-details--collapsed", isOpen);
                detailsPanel.classList.toggle("vetting-details--open", !isOpen);
                if (expandBtn) {
                    expandBtn.querySelector("i").className = isOpen
                        ? "fa-solid fa-chevron-down"
                        : "fa-solid fa-chevron-up";
                }
            }

            if (headerRow) headerRow.addEventListener("click", toggleDetails);

            // Tailor Application button
            const tailorBtn = card.querySelector(`#btn-tailor-${jobIdx}`);
            if (tailorBtn) {
                tailorBtn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    generateTailoredPackage(jobIdx, job);
                });
            }
        });
    }

    function renderProfile() {
        if (!currentProfile || !currentProfile.summary) {
            profileContainer.innerHTML = `<div class="empty-state"><i class="fa-solid fa-user-gear"></i><p>No profile data available yet.</p></div>`;
            return;
        }

        let html = `
            <div class="profile-header-card" style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 1rem;">
                <div class="profile-avatar"><!-- Icon slot: Add your new profile icon here --></div>
                <div>
                    <h3 style="color: #FFF; font-family: var(--font-display); font-size: 1.3rem;">${escapeHtml(currentProfile.name || 'Candidate Profile')}</h3>
                    <span style="font-size: 0.82rem; color: #94A3B8;">Seniority: <strong style="color: #34D399;">${escapeHtml(currentProfile.seniority || 'Unspecified')}</strong> | Experience: <strong style="color: #60A5FA;">${currentProfile.years_experience || 0} years</strong></span>
                </div>
            </div>

            <div class="profile-sections-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem;">
                <div class="profile-box">
                    <h4>Target Job Titles</h4>
                    <ul class="styled-list">
                        ${(currentProfile.job_titles || []).map(t => `<li><i class="fa-solid fa-bullseye" style="color: var(--accent-cyan);"></i> <span>${escapeHtml(t)}</span></li>`).join('')}
                    </ul>
                </div>
                <div class="profile-box">
                    <h4>Generated Search Queries</h4>
                    <ul class="styled-list">
                        ${(currentProfile.search_queries || []).map(q => `<li><code>${escapeHtml(q)}</code></li>`).join('')}
                    </ul>
                </div>
            </div>

            <div class="profile-section">
                <h4>Extracted Technical Skills & Frameworks</h4>
                <div class="skills-grid">
                    ${(currentProfile.skills || []).map(s => `<span class="skill-tag">${escapeHtml(s)}</span>`).join('')}
                </div>
            </div>
        `;
        profileContainer.innerHTML = html;
    }

    // --- 6. TAILOR APPLICATION MODAL LOGIC ---

    const tailorModal = document.getElementById("tailor-modal");
    const modalLoading = document.getElementById("modal-loading");
    const modalBody = document.getElementById("modal-body");
    const modalFooter = document.getElementById("modal-footer");
    const btnCloseModal = document.getElementById("btn-close-modal");
    const btnRegenerate = document.getElementById("btn-regenerate");
    const modalJobTitle = document.getElementById("modal-job-title");
    const modalJobCompany = document.getElementById("modal-job-company");

    // Track which job is currently shown in the modal
    let activeTailorJobIdx = -1;
    let activeTailorJob = null;

    async function generateTailoredPackage(jobIdx, job) {
        if (!currentSessionId) {
            alert("No active session found. Please run a job search first.");
            return;
        }

        activeTailorJobIdx = jobIdx;
        activeTailorJob = job;

        // Open modal and show loading state
        openTailorModal(job);
        setModalLoading(true);

        const apiBase = (window.location.protocol === "file:") ? "http://127.0.0.1:8000" : "";

        try {
            const formData = new FormData();
            formData.append("session_id", currentSessionId);
            formData.append("job_index", jobIdx);

            const res = await fetch(`${apiBase}/api/tailor-application`, {
                method: "POST",
                body: formData,
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `Server error ${res.status}`);
            }

            const data = await res.json();
            populateModalContent(data);
            setModalLoading(false);

        } catch (err) {
            setModalLoading(false);
            showModalError(err.message);
        }
    }

    function openTailorModal(job) {
        // Set header metadata
        if (modalJobTitle) modalJobTitle.textContent = job.title || "Application Package";
        if (modalJobCompany) {
            const span = modalJobCompany.querySelector("span");
            if (span) span.textContent = job.company || "Hiring Team";
        }

        // Reset tabs to first
        document.querySelectorAll(".modal-tab").forEach((t, i) => {
            t.classList.toggle("active", i === 0);
        });
        document.querySelectorAll(".modal-tab-content").forEach((c, i) => {
            c.classList.toggle("active", i === 0);
        });

        // Reset content
        const coverEl = document.getElementById("asset-cover");
        const outreachEl = document.getElementById("asset-outreach");
        const summaryEl = document.getElementById("asset-ats-summary");
        const highlightsEl = document.getElementById("asset-ats-highlights");
        if (coverEl) coverEl.value = "";
        if (outreachEl) outreachEl.value = "";
        if (summaryEl) summaryEl.textContent = "";
        if (highlightsEl) highlightsEl.innerHTML = "";

        // Show regenerate button hidden until content loaded
        if (btnRegenerate) btnRegenerate.classList.add("hidden");

        // Show modal
        tailorModal.classList.remove("hidden");
        document.body.style.overflow = "hidden";
    }

    function closeTailorModal() {
        tailorModal.classList.add("hidden");
        document.body.style.overflow = "";
    }

    function setModalLoading(isLoading) {
        if (isLoading) {
            modalLoading.classList.remove("hidden");
            modalBody.classList.add("hidden");
            if (modalFooter) modalFooter.classList.add("hidden");
        } else {
            modalLoading.classList.add("hidden");
            modalBody.classList.remove("hidden");
            if (modalFooter) modalFooter.classList.remove("hidden");
        }
    }

    function populateModalContent(data) {
        const coverEl = document.getElementById("asset-cover");
        const outreachEl = document.getElementById("asset-outreach");
        const summaryEl = document.getElementById("asset-ats-summary");
        const highlightsEl = document.getElementById("asset-ats-highlights");

        if (coverEl) coverEl.value = data.cover_letter || "";
        if (outreachEl) outreachEl.value = data.outreach_message || "";
        if (summaryEl) summaryEl.textContent = data.tailored_summary || "";
        if (highlightsEl) {
            highlightsEl.innerHTML = (data.key_highlights || []).map(h =>
                `<li><i class="fa-solid fa-check-circle" style="color:#34D399;flex-shrink:0;"></i><span>${escapeHtml(h)}</span></li>`
            ).join("");
        }
        if (btnRegenerate) btnRegenerate.classList.remove("hidden");
    }

    function showModalError(message) {
        const coverEl = document.getElementById("asset-cover");
        if (coverEl) coverEl.value = `Error generating application: ${message}\n\nPlease try again.`;
        if (btnRegenerate) btnRegenerate.classList.remove("hidden");
        modalBody.classList.remove("hidden");
        if (modalFooter) modalFooter.classList.remove("hidden");
    }

    // Copy-to-clipboard helper with toast feedback
    function copyToClipboard(text, btnEl) {
        navigator.clipboard.writeText(text || "").then(() => {
            const origHtml = btnEl.innerHTML;
            btnEl.innerHTML = '<i class="fa-solid fa-check"></i><span>Copied!</span>';
            btnEl.classList.add("btn-copy-asset--success");
            setTimeout(() => {
                btnEl.innerHTML = origHtml;
                btnEl.classList.remove("btn-copy-asset--success");
            }, 2000);
        }).catch(() => {
            alert("Copy failed. Please select and copy manually.");
        });
    }

    // Modal close button
    if (btnCloseModal) {
        btnCloseModal.addEventListener("click", closeTailorModal);
    }

    // Close modal on backdrop click
    if (tailorModal) {
        tailorModal.addEventListener("click", (e) => {
            if (e.target === tailorModal) closeTailorModal();
        });
    }

    // Close modal on Escape key
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && tailorModal && !tailorModal.classList.contains("hidden")) {
            closeTailorModal();
        }
    });

    // Modal tab switching
    document.querySelectorAll(".modal-tab").forEach(tabBtn => {
        tabBtn.addEventListener("click", () => {
            const targetId = tabBtn.dataset.modaltab;
            document.querySelectorAll(".modal-tab").forEach(t => t.classList.remove("active"));
            document.querySelectorAll(".modal-tab-content").forEach(c => c.classList.remove("active"));
            tabBtn.classList.add("active");
            const targetPanel = document.getElementById(targetId);
            if (targetPanel) targetPanel.classList.add("active");
        });
    });

    // Copy buttons
    const btnCopyCover = document.getElementById("btn-copy-cover");
    const btnCopyOutreach = document.getElementById("btn-copy-outreach");
    const btnCopySummary = document.getElementById("btn-copy-summary");

    if (btnCopyCover) {
        btnCopyCover.addEventListener("click", () => {
            const el = document.getElementById("asset-cover");
            copyToClipboard(el ? el.value : "", btnCopyCover);
        });
    }
    if (btnCopyOutreach) {
        btnCopyOutreach.addEventListener("click", () => {
            const el = document.getElementById("asset-outreach");
            copyToClipboard(el ? el.value : "", btnCopyOutreach);
        });
    }
    if (btnCopySummary) {
        btnCopySummary.addEventListener("click", () => {
            const el = document.getElementById("asset-ats-summary");
            copyToClipboard(el ? el.textContent : "", btnCopySummary);
        });
    }

    // Regenerate button
    if (btnRegenerate) {
        btnRegenerate.addEventListener("click", () => {
            if (activeTailorJobIdx >= 0 && activeTailorJob) {
                generateTailoredPackage(activeTailorJobIdx, activeTailorJob);
            }
        });
    }

    // --- TAB SWITCHING & FILTERS ---
    tabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            tabBtns.forEach(b => b.classList.remove("active"));
            tabContents.forEach(c => c.classList.remove("active"));

            btn.classList.add("active");
            document.getElementById(btn.dataset.tab).classList.add("active");
        });
    });

    filterPills.forEach(pill => {
        pill.addEventListener("click", () => {
            filterPills.forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            activeFilter = pill.dataset.filter;
            renderJobs();
        });
    });

    jobSearchInput.addEventListener("input", renderJobs);

    // Helper functions
    function setRunningState(isRunning) {
        btnStart.disabled = isRunning;
        if (isRunning) {
            btnStart.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Agent Running...`;
        } else {
            btnStart.innerHTML = `Launch AI Job Agent`;
        }
    }

    function resetDashboard() {
        terminalBody.innerHTML = "";
        totalLogLines = 0;
        allLogs = [];
        logCountSpan.textContent = "0 lines";
        actionBanner.classList.add("hidden");
        updatePhaseStepper("Phase 1: Resume Analysis");
    }

    function escapeHtml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }
});
