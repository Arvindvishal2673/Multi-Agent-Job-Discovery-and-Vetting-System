import os
import tempfile
import threading
import time
import queue
import logging
import streamlit as st
import pandas as pd

from job_hunter.orchestrator import ResumeJobOrchestrator
from job_hunter.models import JobSearchCriteria

# Streamlit App Configurations
st.set_page_config(
    page_title="AI Job Hunter Dashboard",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Premium Styling
st.markdown("""
<style>
    .main-header {
        font-family: 'Outfit', 'Inter', sans-serif;
        font-size: 2.8rem;
        font-weight: 800;
        background: linear-gradient(135deg, #3A1C71 0%, #D76D77 50%, #FFAF7B 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-family: 'Inter', sans-serif;
        font-size: 1.1rem;
        color: #A0AEC0;
        margin-bottom: 2rem;
    }
    .metric-box {
        background-color: #1A202C;
        border: 1px solid #2D3748;
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        transition: transform 0.2s ease-in-out;
    }
    .metric-box:hover {
        transform: translateY(-2px);
        border-color: #4A5568;
    }
    .metric-val {
        font-size: 2rem;
        font-weight: 700;
        color: #ED8936;
    }
    .metric-label {
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: #718096;
        margin-top: 0.5rem;
    }
    .badge-strong {
        background-color: #22543D;
        color: #C6F6D5;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-decent {
        background-color: #744210;
        color: #FEFCBF;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-weak {
        background-color: #742A2A;
        color: #FED7D7;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- SESSION STATE & LOG QUEUE SETUP -----------------
GLOBAL_LOG_QUEUE = queue.Queue()

def get_log_queue():
    if "log_queue" not in st.session_state or st.session_state.log_queue is None:
        st.session_state["log_queue"] = GLOBAL_LOG_QUEUE
    return st.session_state.get("log_queue", GLOBAL_LOG_QUEUE)

if "result" not in st.session_state:
    st.session_state.result = None
if "logs" not in st.session_state:
    st.session_state.logs = []
if "running" not in st.session_state:
    st.session_state.running = False
st.session_state["log_queue"] = get_log_queue()

# ----------------- COMPREHENSIVE LOGGING INTERCEPTOR -----------------
class StreamlitLogHandler(logging.Handler):
    def emit(self, record):
        try:
            log_entry = self.format(record)
            GLOBAL_LOG_QUEUE.put(log_entry)
            get_log_queue().put(log_entry)
        except Exception:
            pass

# Intercept both Root logger and job_hunter logger so ALL system, network, agent, and error logs appear live
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
jh_logger = logging.getLogger("job_hunter")
jh_logger.setLevel(logging.INFO)

for l in [root_logger, jh_logger]:
    for h in list(l.handlers):
        if h.__class__.__name__ == "StreamlitLogHandler":
            l.removeHandler(h)

handler = StreamlitLogHandler()
handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s [%(name)s]: %(message)s", datefmt="%H:%M:%S"))
root_logger.addHandler(handler)
jh_logger.addHandler(handler)
jh_logger.propagate = False

# Helper for badges
def get_fit_badge_html(decision):
    if decision == "Strong Fit":
        return '<span class="badge-strong">Strong Fit</span>'
    elif decision == "Decent Fit":
        return '<span class="badge-decent">Decent Fit</span>'
    else:
        return '<span class="badge-weak">Weak Fit</span>'

# ----------------- UI LAYOUT -----------------
st.markdown('<div class="main-header">💼 AI Job Hunter</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Upload your resume, define criteria, and watch the AI search & vet matching jobs in real-time.</div>', unsafe_allow_html=True)

# --- SIDEBAR: Configuration & Upload ---
st.sidebar.header("🎯 Job Search Settings")

uploaded_file = st.sidebar.file_uploader(
    "Upload Resume (.pdf, .txt, .md)", 
    type=["pdf", "txt", "md"],
    help="Upload your resume. The AI will extract your skills, summary, and target job titles."
)

st.sidebar.subheader("🔍 Filters & Options")

# Locations
locations_input = st.sidebar.text_input(
    "Preferred Locations", 
    value="Remote", 
    help="Comma-separated list of locations (e.g. Remote, Germany, London)"
)
locations = [loc.strip() for loc in locations_input.split(",") if loc.strip()]

# Keywords
keywords_input = st.sidebar.text_input(
    "Extra Search Keywords", 
    value="", 
    help="Comma-separated extra search terms to force (e.g. PyTorch, React)"
)
keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]

# Remote only
remote_only = st.sidebar.checkbox(
    "Remote Only", 
    value=True,
    help="If checked, only lists jobs with 'remote' in the description or location."
)

# Target India Only
target_india_only = st.sidebar.checkbox(
    "Target India Jobs Only",
    value=True,
    help="If checked, skips foreign-only boards (Remotive, RemoteOK, Arbeitnow) and searches Indian portals (Naukri, Instahyre, Internshala, Cuvette, Wellfound, LinkedIn India, Indeed India) using regional filters for the latest jobs in the past month."
)

# Max evals (Cost control)
max_evals = st.sidebar.slider(
    "Max Vetting Evaluations", 
    min_value=5, 
    max_value=100, 
    value=15, 
    step=5,
    help="Limits the number of parallel LLM calls to screen jobs. Helps stay within Groq rate limits."
)

# Minimum salary
min_salary = st.sidebar.number_input(
    "Minimum Annual Salary ($)", 
    min_value=0, 
    value=0, 
    step=10000,
    help="Threshold to filter jobs by salary, if available in the job post."
)

# Start button
start_button = st.sidebar.button(
    "🚀 Start Agent Job Search", 
    disabled=st.session_state.running or uploaded_file is None,
    use_container_width=True
)

if uploaded_file is None:
    st.sidebar.warning("⚠️ Please upload a resume to enable search.")

# --- MAIN WORKSPACE TABS ---
tab_dashboard, tab_jobs, tab_profile = st.tabs([
    "📈 Run Dashboard", 
    "💼 Vetted Job Matches", 
    "👤 Candidate Profile"
])

# ----------------- TAB 1: RUN DASHBOARD -----------------
with tab_dashboard:
    if start_button and uploaded_file is not None:
        # Reset state
        st.session_state.result = None
        st.session_state.logs = []
        st.session_state.running = True
        
        # Clear out queues
        for q in [st.session_state.log_queue, GLOBAL_LOG_QUEUE]:
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        
        # Save uploaded file to a temporary file
        file_suffix = os.path.splitext(uploaded_file.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name
        
        # Prep criteria
        criteria = JobSearchCriteria(
            keywords=keywords,
            locations=locations,
            remote_only=remote_only,
            min_salary=min_salary if min_salary > 0 else None,
            target_india_only=target_india_only
        )
        
        # Output excel path
        output_dir = "outputs"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "job_matches.xlsx")
        
        # Define background execution target
        result_holder = {}
        def execution_thread():
            try:
                logging.info("🚀 Starting ResumeJobOrchestrator pipeline execution...")
                orchestrator = ResumeJobOrchestrator()
                res = orchestrator.run(
                    resume_path=tmp_path,
                    criteria=criteria,
                    max_evals=max_evals,
                    output_path=output_path
                )
                result_holder["result"] = res
                logging.info("✅ ResumeJobOrchestrator execution finished successfully.")
            except Exception as e:
                import traceback
                error_traceback = traceback.format_exc()
                logging.error("❌ CRITICAL PIPELINE FAILURE:\n%s", error_traceback)
                result_holder["error"] = str(e)
                result_holder["traceback"] = error_traceback
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        # Start thread
        thread = threading.Thread(target=execution_thread)
        thread.start()
        
        # UI components during run
        st.info("🤖 **Agent Pipeline is Running...** Live debug logs streaming below:")
        progress_bar = st.progress(0.1)
        status_text = st.empty()
        log_code_box = st.empty()
        
        # Stream logs & wait for completion safely
        while thread.is_alive():
            # Pull any new logs safely from both queues
            lq = get_log_queue()
            while not lq.empty():
                st.session_state.logs.append(lq.get())
            while not GLOBAL_LOG_QUEUE.empty():
                item = GLOBAL_LOG_QUEUE.get()
                if item not in st.session_state.logs:
                    st.session_state.logs.append(item)
            
            # Simple progress heuristics
            log_str = "\n".join(st.session_state.logs)
            if "Phase 4" in log_str:
                progress_bar.progress(0.9)
                status_text.write("Writing report and saving results...")
            elif "Phase 3" in log_str:
                progress_bar.progress(0.7)
                status_text.write("Vetting job listings against candidate profile using Groq LLM...")
            elif "ReAct iteration" in log_str:
                progress_bar.progress(0.55)
                status_text.write("🔄 ReAct loop: LLM is evaluating result quality and refining queries...")
            elif "Phase 2a" in log_str:
                progress_bar.progress(0.35)
                status_text.write("🧠 PlannerAgent: LLM selecting optimal job sources via tool-calling...")
            elif "Phase 2" in log_str:
                progress_bar.progress(0.3)
                status_text.write("Querying LLM-selected job boards in parallel...")
            else:
                progress_bar.progress(0.15)
                status_text.write("Parsing resume and generating search terms...")
                
            log_code_box.code(log_str[-12000:], language="text") # Limit visible characters for performance
            time.sleep(0.2)
            
        thread.join()
        
        # Fetch final logs safely
        lq = get_log_queue()
        while not lq.empty():
            st.session_state.logs.append(lq.get())
        while not GLOBAL_LOG_QUEUE.empty():
            item = GLOBAL_LOG_QUEUE.get()
            if item not in st.session_state.logs:
                st.session_state.logs.append(item)
        
        final_log_str = "\n".join(st.session_state.logs)
        log_code_box.code(final_log_str, language="text")
        progress_bar.progress(1.0)
        
        st.session_state.running = False
        
        if "error" in result_holder:
            st.error(f"❌ Job search failed: **{result_holder['error']}**")
            if "traceback" in result_holder:
                with st.expander("🚨 View Full Python Error Traceback for Debugging"):
                    st.code(result_holder["traceback"], language="python")
        else:
            st.success("🎉 Job Search completed successfully!")
            st.session_state.result = result_holder["result"]
            
    # Render log download button whenever logs exist
    if st.session_state.logs:
        full_logs_txt = "\n".join(st.session_state.logs)
        st.download_button(
            label="📥 Download Full Debug Logs (.txt)",
            data=full_logs_txt,
            file_name="agent_execution_debug.log",
            mime="text/plain",
            use_container_width=True
        )
            
    # Show stats / summary if result exists
    if st.session_state.result:
        metrics = st.session_state.result["metrics"]
        
        st.subheader("📊 Execution Summary")
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        
        with col1:
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-val">{metrics['total_found']}</div>
                <div class="metric-label">Total Found</div>
            </div>
            """, unsafe_allow_html=True)
            
        with col2:
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-val">{metrics['evaluated']}</div>
                <div class="metric-label">Evaluated / Vetted</div>
            </div>
            """, unsafe_allow_html=True)
            
        with col3:
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-val" style="color: #48BB78;">{metrics['strong_fits']}</div>
                <div class="metric-label">Strong Fits</div>
            </div>
            """, unsafe_allow_html=True)
            
        with col4:
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-val" style="color: #4299E1;">{metrics['elapsed_seconds']}s</div>
                <div class="metric-label">Time Elapsed</div>
            </div>
            """, unsafe_allow_html=True)

        with col5:
            sources_count = len(metrics.get('activated_sources', []))
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-val" style="color: #B794F4;">{sources_count}</div>
                <div class="metric-label">Sources (LLM Picked)</div>
            </div>
            """, unsafe_allow_html=True)

        with col6:
            react_iters = metrics.get('react_iterations', 0)
            st.markdown(f"""
            <div class="metric-box">
                <div class="metric-val" style="color: #F6AD55;">{react_iters}</div>
                <div class="metric-label">ReAct Iterations</div>
            </div>
            """, unsafe_allow_html=True)
            
        st.write("")
        st.subheader("📋 Final Action & Agent Log Output")
        st.code("\n".join(st.session_state.logs))
        
        # Download Excel button
        xls_path = st.session_state.result["output_path"]
        if xls_path and os.path.exists(xls_path):
            with open(xls_path, "rb") as f:
                st.download_button(
                    label="📥 Download Vetted Jobs Excel Report",
                    data=f,
                    file_name="job_matches.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
    else:
        st.write("Configure settings in the sidebar and press **Start Agent Job Search** to begin!")

# ----------------- TAB 2: VETTED JOB MATCHES -----------------
with tab_jobs:
    if st.session_state.result and st.session_state.result["jobs"]:
        jobs = st.session_state.result["jobs"]
        
        st.subheader("🎯 Vetted Jobs List")
        st.write("Below are the job listings found, vetted, and scored by the agent's LLM vetting process.")
        
        # Dataframe summary
        df_list = []
        for idx, job in enumerate(jobs):
            df_list.append({
                "Index": idx + 1,
                "Title": job.title,
                "Company": job.company or "Unknown",
                "Location": job.location or "Unknown",
                "Source": job.source,
                "Fit Score": job.fit_score,
                "Fit Decision": job.fit_decision
            })
        df = pd.DataFrame(df_list)
        st.dataframe(df.set_index("Index"), use_container_width=True)
        
        st.write("---")
        st.subheader("🔍 Deep-Dive: Action & Reasoning Details")
        st.info("Click on any job listing below to see the LLM's detailed reasoning, requirements fit, and gaps identified.")
        
        for idx, job in enumerate(jobs):
            # Design header with inline CSS
            badge_html = get_fit_badge_html(job.fit_decision)
            header_text = f"**{job.title}** at **{job.company}** — Score: `{int(job.fit_score)}/100`"
            
            with st.expander(f"{idx+1}. {job.title} ({job.company}) — {job.fit_decision} ({int(job.fit_score)}/100)"):
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.markdown(f"### {job.title}")
                    st.markdown(f"**Company:** {job.company or 'Unknown'} | **Location:** {job.location or 'Unknown'}")
                    st.markdown(f"**Source:** `{job.source}` | [Open Original Listing]({job.url})")
                    
                    st.markdown("#### 💡 Agent Fit Reasons")
                    if job.fit_reasons:
                        for r in job.fit_reasons:
                            st.write(f"✅ {r}")
                    else:
                        st.write("_No fit reasons provided._")
                        
                    st.markdown("#### ⚠️ Identified Gaps")
                    if job.gaps_identified:
                        for g in job.gaps_identified:
                            st.write(f"🔍 {g}")
                    else:
                        st.write("_No gaps identified! Excellent match._")
                
                with col2:
                    st.markdown("<div style='text-align:center;'>", unsafe_allow_html=True)
                    st.markdown(f"#### Fit Decision")
                    st.markdown(f"<div style='font-size:1.5rem; margin-bottom:1rem;'>{badge_html}</div>", unsafe_allow_html=True)
                    st.metric("Fit Score", f"{int(job.fit_score)}/100")
                    st.markdown("</div>", unsafe_allow_html=True)
                
                st.markdown("---")
                st.markdown("**Job Description Preview:**")
                st.text_area("Description Text", job.description, height=200, disabled=True, key=f"desc_{idx}")
                
    elif st.session_state.result:
        st.info("No matching jobs passed pre-filtering or vetting.")
    else:
        st.info("Please run the search first to inspect the matching jobs.")

# ----------------- TAB 3: CANDIDATE PROFILE -----------------
with tab_profile:
    if st.session_state.result and st.session_state.result["profile"]:
        profile = st.session_state.result["profile"]
        
        st.subheader("👤 Extracted Candidate Profile")
        st.write("This is the candidate profile generated by the `ResumeAnalyzer` agent from your uploaded resume.")
        
        st.markdown("### 📝 Professional Summary")
        st.write(profile.summary)
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### 🏆 Extracted Seniority")
            st.markdown(f"**{profile.seniority}**")
            
            st.markdown("### 🎯 Recommended Target Job Titles")
            for t in profile.job_titles:
                st.markdown(f"- {t}")
        
        with col2:
            st.markdown("### 🔍 Optimized Search Queries")
            for q in profile.search_queries:
                st.markdown(f"- `{q}`")
                
        st.markdown("### 🛠️ Extracted Skills")
        skills_html = "".join([f'<span style="background-color: #2D3748; color: #E2E8F0; padding: 0.3rem 0.6rem; border-radius: 6px; margin: 0.2rem; display: inline-block; font-size: 0.9rem;">{skill}</span>' for skill in profile.skills])
        st.markdown(skills_html, unsafe_allow_html=True)

        st.markdown("### 🤖 Agentic Decisions")
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**🧠 LLM-Selected Job Sources** *(PlannerAgent)*")
            if profile.activated_sources:
                for src in profile.activated_sources:
                    st.markdown(f"- `{src}`")
            else:
                st.write("_Not available (run search first)._")
        with col_b:
            st.markdown("**🔄 ReAct Loop Iterations** *(ReflectionAgent)*")
            st.metric("Total Iterations", profile.react_iterations)
            st.caption("Each iteration the LLM observed results and decided to refine or proceed.")
        
        st.write("")
        with st.expander("📄 Raw Extracted Text"):
            st.text_area("Raw text", profile.raw_text, height=400, disabled=True)
            
    elif st.session_state.result:
        st.warning("Candidate profile extraction details are missing.")
    else:
        st.info("Upload your resume and run the search to view your extracted profile.")
