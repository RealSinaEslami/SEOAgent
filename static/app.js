"use strict";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const fa = n => Number(n || 0).toLocaleString("fa-IR");
const date = value => value ? new Date(value).toLocaleDateString("fa-IR", {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "—";
const names = {dashboard:"نمای کلی", topics:"ایده‌ها و موضوعات", articles:"کتابخانه مقالات", site:"شناخت و اتصال سایت", activity:"گزارش فعالیت‌ها", settings:"تنظیمات مدل"};
const statuses = {draft:["نیازمند بازبینی","amber"],ready:["تأییدشده","green"],wp_draft:["پیش‌نویس وردپرس","blue"],published:["منتشرشده","green"],scheduled:["زمان‌بندی‌شده","blue"],syncing:["در حال ارسال","blue"],uncertain:["نیازمند بررسی ارسال","red"]};
const intents = {informational:"آموزشی",commercial:"بررسی و مقایسه",transactional:"خرید و اقدام"};
const jobNames = {analyze:"تحلیل سایت",generate:"تولید مقاله",pipeline:"جریان تولید محتوا",sync:"ارسال به وردپرس"};
const state = {sites:[],site:null,topics:[],articles:[],activity:{jobs:[],events:[]},bootstrap:null,route:"dashboard",filter:"all",search:"",editor:null,timer:null,refreshing:false};
let toastTimer;

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {cache:"no-store", ...options, headers:{"Content-Type":"application/json", "X-CSRF-Token":state.bootstrap?.csrf_token || "", ...options.headers}, body:options.body === undefined ? undefined : JSON.stringify(options.body)});
  let result;
  try { result = await response.json(); } catch { throw new Error("پاسخ سرور قابل خواندن نیست."); }
  if (!response.ok) throw new Error(result.detail || "عملیات انجام نشد.");
  return result;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const target = $("#toast"); target.textContent = message; target.className = `show${error ? " error" : ""}`;
  toastTimer = setTimeout(() => {target.className = "";}, 6500);
}

function badge(status) { const s = statuses[status] || [status,""]; return `<span class="badge dot ${s[1]}">${e(s[0])}</span>`; }
function heading(title, description, action = "", kicker = "CONTENT WORKSPACE") { return `<div class="page-heading"><div><div class="eyebrow">${kicker}</div><h1>${title}</h1><p>${description}</p></div>${action}</div>`; }
function button(label, action, glyph = "plus", variant = "") {return `<button class="button ${variant}" data-action="${action}">${icon(glyph)}${label}</button>`;}
function empty(title, message, action = "", glyph = "file") {return `<div class="empty"><div class="empty-icon">${icon(glyph)}</div><h3>${title}</h3><p>${message}</p>${action}</div>`;}
function safeLink(value) { try {const u=new URL(value); return u.protocol==="https:" ? e(u.href) : "#";} catch{return "#";} }
function activeJob() {return state.activity.jobs.find(j => ["queued","running"].includes(j.status));}
function jobBanner() {const j=activeJob();return j ? `<div class="job-banner"><div class="spinner"></div><div class="job-body"><p>${e(j.message)} <span class="word-counter">· ${fa(j.progress)}٪</span></p><div class="progress-track"><span style="width:${Number(j.progress)}%"></span></div></div><button class="button ghost small" data-cancel="${e(j.id)}">${j.cancel_requested ? "در انتظار توقف…" : "توقف"}</button></div>` : "";}

async function refresh() {
  if(state.refreshing) return;
  state.refreshing=true;
  try {
    state.sites=await api("/sites");
    const selected=localStorage.getItem("roshd-site");
    state.site=state.sites.find(s=>s.id===selected) || state.sites[0] || null;
    if(state.site) {
      const id=state.site.id;
      [state.topics,state.articles,state.activity]=await Promise.all([api(`/sites/${id}/topics`),api(`/sites/${id}/articles`),api(`/sites/${id}/activity`)]);
    } else {state.topics=[];state.articles=[];state.activity={jobs:[],events:[]};}
    render();
    clearTimeout(state.timer);
    if(activeJob()) state.timer=setTimeout(poll,2500);
  } finally {state.refreshing=false;}
}

async function poll() {
  const previous=activeJob()?.id;
  try {await refresh(); if(previous && !activeJob()) {const finished=state.activity.jobs.find(j=>j.id===previous); toast(finished?.error || (finished?.status==="cancelled" ? "عملیات متوقف شد." : "پردازش با موفقیت انجام شد."),finished?.status==="failed");}}
  catch(err) {toast(err.message,true);state.timer=setTimeout(poll,6000);}
}

function render() {
  state.route=names[location.hash.slice(1)] ? location.hash.slice(1) : "dashboard";
  $$("[data-nav]").forEach(a=>a.classList.toggle("active",a.dataset.nav===state.route));
  $("#page-breadcrumb").textContent=names[state.route];
  $("#article-count").textContent=fa(state.articles.length);
  $("#key-dot").classList.toggle("connected",state.bootstrap.settings.has_api_key);
  $("#side-site").innerHTML=state.site ? `${e(state.site.name)}<small>${e(new URL(state.site.url).hostname)}</small>` : "سایت خود را متصل کنید<small>شروع یک مسیر تازه</small>";
  const views={dashboard:dashboardView,topics:topicsView,articles:articlesView,site:siteView,activity:activityView,settings:settingsView};
  const notice=state.site?.demo ? `<div class="demo-notice">${icon("spark")}پروژه نمونه — اطلاعات و محتوای این سایت نمایشی‌اند و به سایتی واقعی ارسال نمی‌شوند.</div>` : "";
  $("#main").innerHTML=(state.route!=="settings" ? notice+jobBanner() : "")+views[state.route]();
}

function hero(hasSite) {
  return `<section class="hero"><div class="hero-content"><div class="hero-kicker"><span></span>GROW WITH PURPOSE</div><h2>${hasSite ? "از یک ایده خوب، <em>تا محتوایی که اثر می‌گذارد.</em>" : "یک مسیر تازه برای <em>رشد محتوای سایت شما.</em>"}</h2><p>${hasSite ? "سایتتان را بشناسید، موضوع‌های مرتبط پیدا کنید و مقاله بعدی را با دقت بسازید." : "آدرس سایت را وارد کنید؛ شناخت مخاطب، ایده‌پردازی و نگارش را به رشد بسپارید."}</p><div class="actions">${button(hasSite?"شروع جریان تولید":"افزودن اولین سایت",hasSite?"pipeline":"new-site",hasSite?"spark":"plus","lime")}${!hasSite ? `<button class="button ghost" style="color:#d5e3ca" data-action="demo">دیدن پروژه نمونه ${icon("arrow")}</button>` : `<a class="button ghost" style="color:#d5e3ca" href="#topics">بررسی ایده‌ها ${icon("arrow")}</a>`}</div><div class="hero-chips"><span>${icon("check")}متناسب با کسب‌وکار شما</span><span>${icon("check")}انتشار با تأیید شما</span><span>${icon("check")}کنترل کامل محتوا</span></div></div><div class="hero-art" aria-hidden="true"><svg viewBox="0 0 330 270"><g stroke="#7b9d78" fill="none"><ellipse cx="148" cy="240" rx="160" ry="95"/><ellipse cx="148" cy="240" rx="128" ry="75"/><ellipse cx="148" cy="240" rx="96" ry="55"/><path d="M149 240V75m0 92c-63 2-90-30-90-77 61 0 90 31 90 77Zm0-39c54 0 91-39 91-86-60 1-91 40-91 86Zm0 75c54 0 91-30 91-74-58 0-91 31-91 74Z"/><path d="m149 167-61-51m61 11 63-59m-63 135 63-51"/></g><g fill="#adc893"><circle cx="44" cy="62" r="3"/><circle cx="277" cy="123" r="3"/><path d="m262 38 2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/></g></svg></div></section>`;
}

function dashboardView() {
  const site=state.site;
  let out=heading("فضایی برای محتوای بهتر", "از شناخت سایت تا انتشار؛ تمام مسیر محتوای شما در یک نگاه.",button("افزودن سایت","new-site","plus","secondary"),"سلام، به رشد خوش آمدید");
  out+=hero(!!site);
  if(!site) return out+`<div class="onboarding-grid"><div class="onboarding-step"><span class="step-label">01 / CONNECT</span><h3>سایت خود را معرفی کنید</h3><p>آدرس سایت و چند خط درباره کسب‌وکار و مخاطبانتان؛ نقطه شروع همین‌جاست.</p></div><div class="onboarding-step"><span class="step-label">02 / CREATE</span><h3>محتوای مرتبط بسازید</h3><p>پیشنهاد موضوع بر اساس صفحات سایت، نگارش مقاله و بررسی ساختار محتوا.</p></div><div class="onboarding-step"><span class="step-label">03 / PUBLISH</span><h3>با اطمینان منتشر کنید</h3><p>متن و منابع را بازبینی کنید؛ پیش‌نویس را به وردپرس بفرستید و زمان انتشار را انتخاب کنید.</p></div></div>`;
  const count=status=>state.articles.filter(a=>status.includes(a.status)).length;
  const metrics=[["مقاله‌های فضای کار",state.articles.length,"محتوای ذخیره‌شده در این پروژه","file"],["منتظر بازبینی",count(["draft"]),"آماده بررسی و ویرایش شما","clock"],["ارسال‌شده به وردپرس",state.articles.filter(a=>a.wp_id).length,"پیش‌نویس، منتشر یا زمان‌بندی‌شده","send"],["ایده‌های آماده",state.topics.filter(t=>t.status==="idea").length,"فرصت‌های پیشنهادی برای محتوای بعدی","spark"]];
  out+=`<div class="stats">${metrics.map(([label,note,sub,glyph])=>`<div class="stat"><div class="stat-top">${label}<span class="stat-icon">${icon(glyph)}</span></div><strong>${fa(note)}</strong><small>${sub}</small></div>`).join("")}</div>`;
  const flow=[!!site,!!site.analysis.pages?.length,!!state.articles.length,!!state.articles.some(a=>a.wp_id)];
  out+=`<div class="two-columns"><div><section class="panel"><div class="panel-header"><h2>مسیر تولید محتوای شما</h2><span class="badge green">${fa(flow.filter(Boolean).length)} از ۴ مرحله</span></div><div class="flow">${["شناخت سایت","انتخاب موضوع","نگارش و بازبینی","ارسال به وردپرس"].map((s,i)=>`<div class="flow-step ${flow[i]?"done":""}"><div class="flow-number">${flow[i]?icon("check"):fa(i+1)}</div>${s}</div>`).join("")}</div></section><section class="panel"><div class="panel-header"><h2>آخرین مقاله‌ها</h2><a class="panel-link" href="#articles">همه مقاله‌ها ${icon("arrow")}</a></div>${state.articles.length?state.articles.slice(0,4).map(articleRow).join(""):empty("اولین مقاله شما اینجا قرار می‌گیرد","پس از تحلیل سایت، یک موضوع انتخاب کنید و نگارش را شروع کنید.",button("پیشنهاد موضوع","analyze","spark","soft"))}</section></div><div><section class="panel"><div class="panel-header"><h2>${icon("globe")}شناخت سایت</h2><a class="panel-link" href="#site">جزئیات ${icon("arrow")}</a></div><div class="panel-body"><div class="mini-site"><span class="site-avatar">${icon("globe")}</span><div><strong>${e(site.name)}</strong><small>${e(new URL(site.url).hostname)}</small></div></div><div class="detail-line"><span>سیستم مدیریت محتوا</span><b>${e(site.analysis.cms||"هنوز بررسی نشده")}</b></div><div class="detail-line"><span>صفحات خوانده‌شده</span><b>${fa(site.analysis.pages?.length)}</b></div><div class="detail-line"><span>اتصال وردپرس</span><b>${site.connection.user?"متصل":"تنظیم نشده"}</b></div><div class="detail-line"><span>آخرین تحلیل</span><b>${date(site.analysis.analyzed_at)}</b></div></div></section><div class="tip"><strong>${icon("leaf")} رشد با محتوای مفید شروع می‌شود</strong><br>کیفیت و ارتباط با نیاز مخاطب را در اولویت بگذارید. تعداد مقاله و امتیاز ساختار، تضمین رتبه در گوگل نیستند.</div></div></div>`;
  return out;
}

function articleRow(a) {return `<div class="article-row" data-article="${e(a.id)}" role="button" tabindex="0"><div class="article-row-main"><span class="article-row-icon">${icon("file")}</span><div><div class="article-row-title">${e(a.title)}</div><div class="article-row-sub"><span>${e(a.keyword)}</span><span>${fa(a.quality.words)} کلمه</span><span>${date(a.updated_at)}</span></div></div></div>${badge(a.status)}</div>`;}

function noSiteView() {return `<section class="panel">${empty("ابتدا سایت خود را اضافه کنید","برای شناخت کسب‌وکار، پیشنهاد موضوع و تولید مقاله، آدرس سایت را ثبت کنید.",button("افزودن سایت","new-site"),"globe")}</section>`;}

function topicsView() {
  let out=heading("ایده‌ای برای قدم بعدی", "موضوع‌های مرتبط با سایت؛ هر پیشنهاد با هدف و ساختار مشخص.",`<div class="actions">${button("موضوع دلخواه","new-topic","plus","secondary")}${button("تحلیل و پیشنهاد","analyze","spark")}</div>`,"CONTENT IDEAS");
  if(!state.site) return out+noSiteView();
  out+=`<div class="notice neutral">پیشنهادها از محتوای خوانده‌شده سایت و اطلاعات کسب‌وکار ساخته می‌شوند. حجم جست‌وجو و رتبه رقبا در این نسخه اندازه‌گیری نمی‌شوند.</div>`;
  if(!state.topics.length) return out+`<section class="panel">${empty("فرصت‌های محتوایی را پیدا کنیم","سایت را تحلیل کنید تا موضوع‌های متناسب با مخاطب پیشنهاد شوند؛ یا موضوع دلخواه خود را وارد کنید.",button("شروع تحلیل","analyze","spark"),"spark")}</section>`;
  return out+`<div class="topic-grid">${state.topics.map(t=>`<article class="topic-card"><div class="actions"><span class="badge ${t.status==="drafted"?"green":""}">${t.status==="drafted"?"مقاله ساخته شده":"ایده پیشنهادی"}</span><span class="badge">${e(intents[t.intent])}</span></div><h3>${e(t.title)}</h3><p>${e(t.rationale)}</p>${t.outline.length?`<ul>${t.outline.slice(0,4).map(s=>`<li>${e(s)}</li>`).join("")}</ul>`:""}<div class="actions"><span class="keyword">${e(t.keyword)}</span>${t.status==="drafted"?`<a class="panel-link" href="#articles">دیدن مقاله ${icon("arrow")}</a>`:`<button class="button soft small" data-generate="${e(t.id)}">${icon("spark")}نگارش مقاله</button>`}</div></article>`).join("")}</div>`;
}

function articlesView() {
  let out=heading("کتابخانه محتوای شما", "مقاله‌ها را بخوانید، ویرایش کنید و برای انتشار آماده کنید.",`<a href="#topics" class="button">${icon("plus")}مقاله جدید</a>`,"YOUR CONTENT LIBRARY");
  if(!state.site) return out+noSiteView();
  out+=`<div class="toolbar"><div class="filters">${[["all","همه مقاله‌ها"],["draft","بازبینی"],["ready","تأییدشده"],["wp_draft","وردپرس"],["published","منتشرشده"]].map(([id,label])=>`<button class="filter ${state.filter===id?"active":""}" data-filter="${id}">${label}</button>`).join("")}</div><label class="searchbox">${icon("search")}<span class="sr-only">جست‌وجوی مقاله</span><input id="article-search" placeholder="جست‌وجو در عنوان و کلیدواژه…" value="${e(state.search)}"></label></div><section class="panel" id="article-list">${articleList()}</section>`;
  return out;
}

function articleList() {
  const list=state.articles.filter(a=>(state.filter==="all"||a.status===state.filter)&&(a.title+" "+a.keyword).toLowerCase().includes(state.search.toLowerCase()));
  if(!list.length) return empty("مقاله‌ای برای نمایش نیست",state.articles.length?"فیلتر یا عبارت جست‌وجو را تغییر دهید.":"با انتخاب یک موضوع، اولین مقاله سایت را بسازید.",`<a href="#topics" class="button soft">${icon("spark")}رفتن به ایده‌ها</a>`);
  return `<div class="table-scroll"><table class="article-table"><thead><tr><th>عنوان مقاله</th><th>کلیدواژه</th><th>تعداد کلمه</th><th>وضعیت</th><th>آخرین تغییر</th><th></th></tr></thead><tbody>${list.map(a=>`<tr data-article="${e(a.id)}" tabindex="0"><td class="table-title">${e(a.title)}</td><td><span class="keyword">${e(a.keyword)}</span></td><td>${fa(a.quality.words)}</td><td>${badge(a.status)}</td><td>${date(a.updated_at)}</td><td>${icon("arrow")}</td></tr>`).join("")}</tbody></table></div>`;
}

function profileFields(profile={}) {return `<label class="field full">درباره کسب‌وکار<textarea name="business" minlength="5" maxlength="4000" required placeholder="چه محصول یا خدمتی دارید و مزیت واقعی شما چیست؟">${e(profile.business||"")}</textarea></label><label class="field full">مخاطب هدف<input name="audience" maxlength="1500" value="${e(profile.audience||"")}" placeholder="مثلاً صاحبان کسب‌وکارهای کوچک"></label><label class="field">زبان محتوا<select name="language">${["فارسی","English","العربية"].map(s=>`<option ${s===profile.language?"selected":""}>${s}</option>`).join("")}</select></label><label class="field">لحن برند<input name="tone" maxlength="200" value="${e(profile.tone||"حرفه‌ای و قابل فهم")}"></label><label class="field">بازار هدف<input name="market" maxlength="100" value="${e(profile.market||"ایران")}"></label><label class="field">موضوع‌ها و ادعاهای ممنوع<input name="forbidden" maxlength="2000" value="${e(profile.forbidden||"")}" placeholder="قیمت نامعتبر، توصیه پزشکی و…"></label>`;}

function siteView() {
  let out=heading("سایت شما، نقطه شروع ما", "اطلاعات دقیق‌تر کسب‌وکار، محتوای مرتبط‌تری می‌سازد.",button("انتخاب سایت","sites","globe","secondary"),"SITE & BRAND");
  const s=state.site;if(!s)return out+noSiteView();
  out+=`<div class="two-columns"><div><section class="panel"><div class="panel-header"><h2>هویت کسب‌وکار</h2><span class="badge">${e(s.name)}</span></div><form class="panel-body" id="profile-form"><div class="form-grid">${profileFields(s.profile)}</div><div class="form-footer"><button class="button" ${s.demo?"disabled":""}>ذخیره اطلاعات</button></div></form></section><section class="panel"><div class="panel-header"><h2>شناخت فعلی سایت</h2>${button("تحلیل دوباره","analyze","spark","secondary small")}</div><div class="panel-body">${s.analysis.summary?`<p class="analysis-summary">${e(s.analysis.summary)}</p><div class="detail-line"><span>مخاطب تشخیص‌داده‌شده</span><b>${e(s.analysis.audience)}</b></div>`:`<p class="metric-description">هنوز تحلیلی انجام نشده است.</p>`}${(s.analysis.warnings||[]).map(w=>`<div class="notice">${e(w)}</div>`).join("")}${s.analysis.pages?.length?`<p class="metric-description">صفحات خوانده‌شده · ${fa(s.analysis.pages.length)}</p>${s.analysis.pages.map(p=>`<a class="source-link" target="_blank" rel="noopener noreferrer" href="${safeLink(p.url)}">${e(p.title||p.url)}${p.noindex?'<span class="badge amber">noindex</span>':""}<small>${e(p.url)}</small></a>`).join("")}`:""}</div></section></div><div><section class="panel"><div class="panel-header"><h2>${icon("link")}اتصال وردپرس</h2>${s.connection.user?'<span class="badge green dot">متصل</span>':'<span class="badge amber">نیازمند تنظیم</span>'}</div><div class="panel-body"><div class="mini-site"><span class="site-avatar">${icon("globe")}</span><div><strong>${e(s.name)}</strong><small>${e(s.url)}</small></div></div>${s.connection.user?`<div class="connection-state">${icon("check")}متصل به حساب ${e(s.connection.user)}</div><div class="detail-line"><span>اجازه انتشار</span><b>${s.connection.can_publish?"فعال":"فقط پیش‌نویس"}</b></div><div class="actions section-gap">${button("تغییر اتصال","connect","link","secondary small")}${button("قطع اتصال","disconnect","close","danger small")}</div>`:`<p class="metric-description">برای انتقال مقاله، یک حساب نویسنده و Application Password مخصوص رشد بسازید.</p>${button("تنظیم و آزمایش اتصال","connect","link","soft")}`}</div></section><div class="tip"><strong>${icon("shield")} دسترسی مخصوص برنامه</strong><br>در وردپرس از بخش کاربران ← شناسنامه ← رمزهای برنامه، یک رمز بسازید. دسترسی در این برنامه رمزنگاری می‌شود. لغو نهایی رمز از پنل وردپرس انجام می‌شود.</div><div class="tip section-gap">عنوان و خلاصه به وردپرس ارسال می‌شوند. انتقال توضیحات متا به Yoast یا Rank Math به اتصال اختصاصی افزونه نیاز دارد؛ این نسخه آن را در خروجی JSON نگه می‌دارد.</div></div></div>`;
  return out;
}

function activityView() {
  let out=heading("هر قدم، ثبت‌شده و روشن", "وضعیت پردازش‌ها و تاریخچه تغییرات این سایت.","","ACTIVITY LOG");
  if(!state.site)return out+noSiteView();
  const jobStatus={queued:["در صف",""],running:["در حال اجرا","blue"],completed:["تکمیل‌شده","green"],failed:["ناموفق","red"],cancelled:["متوقف‌شده","amber"]};
  return out+`<div class="two-columns"><section class="panel"><div class="panel-header"><h2>عملیات پردازش</h2></div>${state.activity.jobs.length?state.activity.jobs.map(j=>`<div class="job-card"><div class="actions"><span>${e(jobNames[j.kind])}</span><span class="badge ${jobStatus[j.status][1]}">${jobStatus[j.status][0]}</span><span class="word-counter">${date(j.created_at)}</span></div><p>${e(j.message)}</p>${j.error?`<div class="error-text">${e(j.error)}</div>`:""}${["queued","running"].includes(j.status)?`<div class="progress-track"><span style="width:${Number(j.progress)}%"></span></div><button class="button ghost small" data-cancel="${e(j.id)}">درخواست توقف</button>`:""}</div>`).join(""):empty("پردازشی ثبت نشده","پس از شروع تحلیل یا تولید مقاله، وضعیت آن را اینجا می‌بینید.","","clock")}</section><section class="panel"><div class="panel-header"><h2>تاریخچه فعالیت‌ها</h2></div><div class="panel-body">${state.activity.events.length?state.activity.events.map(ev=>`<div class="event"><span>${icon("check")}</span><div>${e(ev.message)}<small>${date(ev.created_at)}</small></div></div>`).join(""):"فعالیتی ثبت نشده است."}</div></section></div>`;
}

function settingsView() {
  const s=state.bootstrap.settings;
  return heading("تنظیمات دستیار شما", "کلید و مدل مورد استفاده برای پیشنهاد موضوع و نگارش مقاله.","","MODEL SETTINGS")+`<section class="panel wide-card"><div class="panel-header"><h2>${icon("spark")}اتصال به OpenAI</h2><span class="badge ${s.has_api_key?"green":"amber"}">${s.has_api_key?"کلید تنظیم شده":"نیازمند کلید API"}</span></div><form class="panel-body" id="settings-form"><div class="form-grid"><label class="field full">کلید API<input name="api_key" type="password" autocomplete="new-password" dir="ltr" maxlength="500" placeholder="${s.has_api_key?"برای حفظ کلید فعلی خالی بگذارید":"sk-…"}"><small>کلید رمزنگاری می‌شود و در مرورگر ذخیره نمی‌شود. متن صفحات و مقاله برای پردازش به OpenAI ارسال می‌شود.</small></label><label class="field full">نام مدل<input name="model" dir="ltr" required maxlength="100" pattern="[a-zA-Z0-9._:/\-]+" value="${e(s.model)}"><small>مدل باید Responses API و Structured Outputs را پشتیبانی کند و در حساب شما در دسترس باشد.</small></label><label class="check-label field full"><input name="clear_key" type="checkbox">حذف کلید ذخیره‌شده از برنامه ${s.environment_key?"(کلید فایل .env همچنان فعال می‌ماند)":""}</label></div><div class="notice neutral section-gap">هر اجرا حداکثر ۵ مقاله و هر سایت حداکثر ۱۰ مقاله در روز تولید می‌کند. هزینه سرویس مدل به حساب API شما تعلق می‌گیرد. این نسخه سقف مالی دلاری تعیین نمی‌کند.</div><div class="form-footer"><button class="button">ذخیره تنظیمات</button></div></form></section><div class="tip wide-card">دیتابیس و کلید رمزنگاری در پوشه data نگهداری می‌شوند. این نسخه برای استفاده محلی یک نفر روی همین دستگاه است. برای استفاده تیمی یا اینترنتی به احراز هویت، مدیریت کاربران و استقرار جداگانه نیاز دارد.</div>`;
}

function modal(title,subtitle,content,wide=false) {
  const dialog=$("#modal");dialog.classList.toggle("editor-dialog",wide);
  $("#modal-content").innerHTML=`<div class="modal-header"><div><h2>${title}</h2><p>${subtitle}</p></div><button class="icon-button" data-action="close-modal" aria-label="بستن">${icon("close")}</button></div>${content}`;
  if(!dialog.open)dialog.showModal();
}

function closeModal() {$("#modal").close();state.editor=null;}
function modalError(message) {let target=$(".modal-error",$("#modal"));if(!target){target=document.createElement("div");target.className="modal-error";$(".modal-body",$("#modal"))?.append(target);}if(target)target.textContent=message;}
function profileFrom(data) {return Object.fromEntries(["business","audience","language","tone","market","forbidden"].map(key=>[key,data.get(key)]));}

function newSite() {modal("به سایت شما خوش آمدیم","اطلاعات اولیه را وارد کنید؛ اتصال وردپرس در مرحله بعد تنظیم می‌شود.",`<form class="modal-body" id="new-site-form"><div class="form-grid"><label class="field">نام پروژه<input name="name" required minlength="2" maxlength="100" placeholder="مثلاً فروشگاه من"></label><label class="field">آدرس سایت<input name="url" type="url" required maxlength="1000" dir="ltr" placeholder="https://your-site.com"></label>${profileFields()}</div><div class="modal-error"></div><div class="form-footer"><button type="button" class="button secondary" data-action="close-modal">انصراف</button><button class="button">${icon("plus")}ساخت فضای کار سایت</button></div></form>`);}

function sitesModal() {modal("فضاهای کاری شما","هر سایت، اطلاعات، مقاله‌ها و دسترسی جداگانه دارد.",`<div class="modal-body">${state.sites.map(s=>`<div class="site-list-item"><div><strong>${e(s.name)}</strong><small>${e(s.url)}</small></div><button class="button ${s.id===state.site?.id?"soft":"secondary"} small" data-select-site="${e(s.id)}">${s.id===state.site?.id?"سایت فعلی":"انتخاب"}</button></div>`).join("")}${button("افزودن سایت جدید","new-site","plus")}</div>`);}

function connectModal() {if(!state.site)return newSite();modal("اتصال امن به وردپرس","دسترسی حساب مخصوص نویسنده را وارد کنید.",`<form class="modal-body" id="connect-form"><div class="notice neutral">سایت مقصد: <b dir="ltr">${e(state.site.url)}</b><br>رمز برنامه را از بخش شناسنامه کاربر در وردپرس بسازید.</div><div class="form-grid"><label class="field full">نام کاربری وردپرس<input name="username" required maxlength="100" dir="ltr" autocomplete="off"></label><label class="field full">Application Password<input name="password" type="password" required minlength="10" maxlength="200" dir="ltr" autocomplete="new-password"></label></div><div class="modal-error"></div><div class="form-footer"><button type="button" class="button secondary" data-action="close-modal">انصراف</button><button class="button">${icon("link")}آزمایش و ذخیره اتصال</button></div></form>`);}

function pipelineModal() {if(!state.site)return newSite();modal("جریان تولید محتوای سایت","تحلیل صفحات ← پیشنهاد موضوع ← نگارش ← ذخیره برای بازبینی",`<form class="modal-body" id="pipeline-form"><div class="notice neutral">مقاله‌ها با استفاده از اطلاعات کسب‌وکار و حداکثر ۱۲ صفحه سایت نوشته می‌شوند. پیشنهاد موضوع و نگارش هر مقاله از سرویس مدل استفاده می‌کنند.</div><label class="field">تعداد مقاله در این اجرا<select name="count">${[1,2,3,4,5].map(n=>`<option value="${n}">${fa(n)} مقاله</option>`).join("")}</select></label><label class="check-label section-gap"><input type="checkbox" name="upload_drafts" ${!state.site.connection.user?"disabled":""}>پیش‌نویس مقاله‌ها بعد از تولید به وردپرس ارسال شود.</label>${!state.site.connection.user?'<p class="metric-description">برای این گزینه ابتدا وردپرس را متصل کنید. مقاله‌ها در هر صورت داخل رشد ذخیره می‌شوند.</p>':""}<div class="modal-error"></div><div class="form-footer"><button type="button" class="button secondary" data-action="close-modal">انصراف</button><button class="button">${icon("spark")}شروع پردازش</button></div></form>`);}

function topicModal() {if(!state.site)return newSite();modal("موضوع دلخواه شما","یک موضوع مشخص و کلیدواژه اصلی آن را وارد کنید.",`<form class="modal-body" id="topic-form"><div class="form-grid"><label class="field full">عنوان پیشنهادی<input name="title" required minlength="8" maxlength="200"></label><label class="field full">کلیدواژه اصلی<input name="keyword" required minlength="2" maxlength="120"></label><label class="field full">هدف جست‌وجو<select name="intent">${Object.entries(intents).map(([k,v])=>`<option value="${k}">${v}</option>`).join("")}</select></label></div><div class="modal-error"></div><div class="form-footer"><button class="button">ثبت موضوع</button></div></form>`);}

function generateModal(topicId) {const t=state.topics.find(t=>t.id===topicId);modal("نگارش مقاله","ساختار و متن بر اساس شناخت سایت و منابع تکمیلی نوشته می‌شود.",`<form class="modal-body" id="generate-form" data-topic="${e(topicId)}"><div class="notice neutral">${e(t.title)}</div><label class="field">منابع تکمیلی (اختیاری)<textarea name="sources" class="source-input" placeholder="https://source.com/article&#10;https://another-source.com/guide"></textarea><small>هر URL در یک خط؛ حداکثر ۴ صفحه عمومی HTTPS. فقط صفحاتی که قابل خواندن باشند وارد زمینه مدل می‌شوند.</small></label><div class="tip section-gap">منبعی را وارد کنید که واقعاً درباره موضوع مقاله اطلاعات معتبر دارد. پس از تولید، بررسی صحت ادعاها با شماست.</div><div class="modal-error"></div><div class="form-footer"><button type="button" class="button secondary" data-action="close-modal">انصراف</button><button class="button">${icon("spark")}تولید مقاله</button></div></form>`);}

async function openEditor(id) {
  modal("بازبینی مقاله","در حال خواندن نسخه ذخیره‌شده…",'<div class="modal-loading">در حال بارگذاری…</div>',true);
  const a=await api(`/articles/${id}`);state.editor=a;editorView(a);
}

function editorView(a) {
  const approved=a.approved_revision===a.revision;
  modal("یک قدم تا انتشار",`نسخه ${fa(a.revision)} · ${fa(a.quality.words)} کلمه · ${state.site?.demo?"مقاله نمایشی":"ذخیره‌شده در فضای کار"}`,`<div class="editor-layout"><div class="editor-main"><label class="field"><span class="sr-only">عنوان مقاله</span><input id="edit-title" class="editor-title-input" maxlength="200" value="${e(a.title)}"></label><div class="form-grid"><label class="field full">توضیحات متا<textarea id="edit-meta" maxlength="350" rows="2">${e(a.meta_description)}</textarea></label><label class="field">نامک پیشنهادی (خروجی)<input id="edit-slug" dir="ltr" value="${e(a.slug)}"></label><label class="field">خلاصه<input id="edit-excerpt" maxlength="2000" value="${e(a.excerpt)}"></label></div><div class="editor-tabs"><button class="active" data-editor-tab="preview">پیش‌نمایش</button><button data-editor-tab="html">ویرایش HTML</button><button data-editor-tab="brief">خلاصه سفارش</button></div><iframe id="article-preview" class="article-preview" title="پیش‌نمایش مقاله" sandbox=""></iframe><textarea id="edit-content" class="html-editor" aria-label="متن HTML مقاله" hidden>${e(a.content_html)}</textarea><div id="article-brief" class="tip" hidden>${e(a.brief)}</div></div><aside class="editor-side"><div class="quality-header"><h3>بررسی ساختار مقاله</h3><span class="quality-number">${fa(a.quality.score)}<small style="font-size:12px">/۱۰۰</small></span></div>${a.quality.checks.map(c=>`<div class="check-item ${c.ok?"":"failed"}">${icon(c.ok?"check":"clock")}<span>${e(c.label)}${!c.ok&&c.blocking?' <span class="badge red">نیازمند اصلاح</span>':""}</span></div>`).join("")}<div class="tip">${a.quality.warnings.map(w=>e(w)).join("<br><br>")}</div><h3>منابع استفاده‌شده</h3>${a.sources.length?a.sources.map(s=>`<a class="source-link" href="${safeLink(s.url)}" target="_blank" rel="noopener noreferrer">${e(s.title)}<small>${e(s.url)}</small></a>`).join(""):'<p class="metric-description">منبعی ثبت نشده است.</p>'}<div class="field"><label class="check-label"><input id="approve-check" type="checkbox" ${approved?"checked":""}>متن، ادعاها و منابع نسخه ذخیره‌شده را بررسی کرده‌ام.</label><button class="button secondary small" data-action="approve" ${!a.quality.can_publish?"disabled":""}>${icon("check")}${approved?"نسخه فعلی تأیید شده":"تأیید نسخه فعلی"}</button></div>${a.wp_url?`<a class="source-link" href="${safeLink(a.wp_url)}" target="_blank" rel="noopener noreferrer">مشاهده در وردپرس ${icon("arrow")}</a>`:""}${a.status==="uncertain"?'<div class="notice">نتیجه ارسال قبلی مشخص نیست. «بررسی وضعیت وردپرس» فقط نوشته موجود را پیدا می‌کند.</div>':""}<div class="modal-error"></div></aside></div><div class="editor-bottom"><div class="actions"><button class="button" data-action="save-article">ذخیره تغییرات</button><a class="button secondary small" href="/api/articles/${e(a.id)}/export" download>دریافت JSON</a><span class="editor-status">${badge(a.status)}</span></div><div class="actions"><button class="button secondary" data-sync="draft">${icon("send")}${a.status==="uncertain"?"بررسی وضعیت وردپرس":"ارسال پیش‌نویس"}</button><button class="button soft" data-action="publish-modal" ${!approved||!a.quality.can_publish?"disabled":""}>انتشار / زمان‌بندی</button></div></div>`,true);
  updatePreview(a.content_html);
}

function updatePreview(html) {
  const language=state.site?.profile.language;
  $("#article-preview").srcdoc=`<!doctype html><html lang="${language==="English"?"en":"fa"}" dir="${language==="English"?"ltr":"rtl"}"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>body{font:13px/2.3 Tahoma,sans-serif;color:#3b4939;padding:18px 24px;margin:0}h2{font-size:18px;font-weight:normal;line-height:2;margin-top:30px}h3{font-size:15px}a{color:#63834e}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}p{margin:0 0 18px}</style></head><body>${html}</body></html>`;
}

function editorDirty() {const a=state.editor;return a && ($("#edit-title").value!==a.title||$("#edit-meta").value!==a.meta_description||$("#edit-slug").value!==a.slug||$("#edit-excerpt").value!==a.excerpt||$("#edit-content").value!==a.content_html);}

async function saveArticle() {
  const a=state.editor;
  const result=await api(`/articles/${a.id}`,{method:"PUT",body:{revision:a.revision,title:$("#edit-title").value,meta_description:$("#edit-meta").value,slug:$("#edit-slug").value,excerpt:$("#edit-excerpt").value,content_html:$("#edit-content").value}});
  state.editor=result;editorView(result);await refresh();toast("نسخه جدید ذخیره شد؛ برای انتشار دوباره آن را تأیید کنید.");
}

async function approveArticle() {
  if(editorDirty())throw new Error("ابتدا تغییرات را ذخیره کنید، سپس نسخه جدید را تأیید کنید.");
  if(!$("#approve-check").checked)throw new Error("ابتدا بررسی متن و منابع را تأیید کنید.");
  const a=state.editor;const result=await api(`/articles/${a.id}/approve`,{method:"POST",body:{revision:a.revision,confirmed:true}});
  state.editor=result;editorView(result);await refresh();toast("نسخه فعلی تأیید شد.");
}

function publishModal() {
  if(editorDirty())throw new Error("تغییرات ذخیره‌نشده دارید؛ ابتدا مقاله را ذخیره و دوباره تأیید کنید.");
  const a=state.editor;
  modal("انتشار در سایت","انتشار، مقاله را برای بازدیدکنندگان سایت قابل مشاهده می‌کند.",`<form class="modal-body" id="publish-form" data-article-id="${e(a.id)}" data-revision="${a.revision}"><div class="notice neutral">${e(a.title)}</div><label class="field">روش انتشار<select name="status" id="publish-status"><option value="publish">انتشار همین حالا</option><option value="future">انتشار در زمان مشخص</option></select></label><label class="field schedule-field" id="schedule-wrap" hidden>زمان انتشار (ساعت محلی دستگاه)<input name="scheduled_at" type="datetime-local"></label><label class="field section-gap">دسته‌بندی<select name="category"><option value="">پیش‌فرض وردپرس</option>${(state.site.connection.categories||[]).map(c=>`<option value="${c.id}">${e(c.name)}</option>`).join("")}</select></label><label class="check-label section-gap"><input type="checkbox" name="confirmed" required>انتشار این نسخه از مقاله در سایت ${e(state.site.name)} را تأیید می‌کنم.</label><div class="modal-error"></div><div class="form-footer"><button type="button" class="button secondary" data-action="close-modal">انصراف</button><button class="button">${icon("send")}ثبت انتشار</button></div></form>`);
}

async function handleAction(action) {
  switch(action) {
    case "new-site":return newSite();case "sites":return sitesModal();case "connect":return connectModal();case "pipeline":return pipelineModal();case "new-topic":return topicModal();case "close-modal":return closeModal();
    case "menu":return $("#sidebar").classList.toggle("open");
    case "demo":{const result=await api("/demo",{method:"POST",body:{}});localStorage.setItem("roshd-site",result.id);closeModal();location.hash="dashboard";await refresh();return toast("پروژه نمونه آماده است؛ هیچ سرویس خارجی فراخوانی نشد.");}
    case "analyze":{if(!state.site)return newSite();await api(`/sites/${state.site.id}/analyze`,{method:"POST",body:{}});await refresh();return toast("تحلیل سایت در صف قرار گرفت.");}
    case "disconnect":await api(`/sites/${state.site.id}/connection`,{method:"DELETE"});await refresh();return toast("اطلاعات اتصال از برنامه حذف شد؛ لغو رمز برنامه در پنل وردپرس انجام می‌شود.");
    case "save-article":return saveArticle();case "approve":return approveArticle();case "publish-modal":return publishModal();
  }
}

document.addEventListener("click",async event=>{
  const target=event.target.closest("button,[data-article],[data-nav]");
  if(!target)return;
  if(target.matches("[data-nav]")){$("#sidebar").classList.remove("open");return;}
  if(target.dataset.editorTab) {const mode=target.dataset.editorTab;$$("[data-editor-tab]").forEach(b=>b.classList.toggle("active",b===target));$("#article-preview").hidden=mode!=="preview";$("#edit-content").hidden=mode!=="html";$("#article-brief").hidden=mode!=="brief";if(mode==="preview")updatePreview($("#edit-content").value);return;}
  const oldDisabled=target.disabled;target.disabled=true;
  try {
    if(target.dataset.action)await handleAction(target.dataset.action);
    else if(target.dataset.article)await openEditor(target.dataset.article);
    else if(target.dataset.generate)generateModal(target.dataset.generate);
    else if(target.dataset.filter) {state.filter=target.dataset.filter;render();}
    else if(target.dataset.selectSite) {localStorage.setItem("roshd-site",target.dataset.selectSite);state.filter="all";state.search="";closeModal();await refresh();}
    else if(target.dataset.cancel) {await api(`/jobs/${target.dataset.cancel}/cancel`,{method:"POST",body:{}});await refresh();toast("درخواست توقف ثبت شد؛ مرحله در حال انجام ابتدا به پایان می‌رسد.");}
    else if(target.dataset.sync) {if(editorDirty())throw new Error("ابتدا تغییرات مقاله را ذخیره کنید.");const a=state.editor;await api(`/articles/${a.id}/sync`,{method:"POST",body:{revision:a.revision,status:"draft"}});closeModal();await refresh();toast("عملیات وردپرس در صف قرار گرفت.");}
  } catch(err) {if($("#modal").open)modalError(err.message);else toast(err.message,true);}
  finally {target.disabled=oldDisabled;}
});

document.addEventListener("submit",async event=>{
  event.preventDefault();const form=event.target;const submit=$("button:not([type=button])",form);const data=new FormData(form);if(submit)submit.disabled=true;
  try {
    if(form.id==="new-site-form") {const result=await api("/sites",{method:"POST",body:{name:data.get("name"),url:data.get("url"),profile:profileFrom(data)}});localStorage.setItem("roshd-site",result.id);closeModal();location.hash="site";await refresh();toast("سایت اضافه شد؛ اطلاعات اتصال را تنظیم کنید.");}
    else if(form.id==="profile-form") {await api(`/sites/${state.site.id}/profile`,{method:"PUT",body:profileFrom(data)});await refresh();toast("اطلاعات کسب‌وکار ذخیره شد.");}
    else if(form.id==="settings-form") {await api("/settings",{method:"PUT",body:{api_key:data.get("api_key")||null,model:data.get("model"),clear_key:data.has("clear_key")}});state.bootstrap=await api("/bootstrap");render();toast("تنظیمات ذخیره شد.");}
    else if(form.id==="connect-form") {await api(`/sites/${state.site.id}/connect`,{method:"POST",body:Object.fromEntries(data)});closeModal();await refresh();toast("اتصال وردپرس برقرار شد.");}
    else if(form.id==="pipeline-form") {await api(`/sites/${state.site.id}/pipeline`,{method:"POST",body:{count:Number(data.get("count")),upload_drafts:data.has("upload_drafts")}});closeModal();location.hash="dashboard";await refresh();toast("جریان تولید محتوا شروع شد.");}
    else if(form.id==="topic-form") {await api(`/sites/${state.site.id}/topics`,{method:"POST",body:Object.fromEntries(data)});closeModal();await refresh();toast("موضوع ثبت شد.");}
    else if(form.id==="generate-form") {const sources=String(data.get("sources")).split(/\r?\n/).map(s=>s.trim()).filter(Boolean);await api(`/sites/${state.site.id}/generate`,{method:"POST",body:{topic_id:form.dataset.topic,sources}});closeModal();await refresh();toast("نگارش مقاله در صف قرار گرفت.");}
    else if(form.id==="publish-form") {const body={revision:Number(form.dataset.revision),status:data.get("status"),confirmed:data.has("confirmed"),category:data.get("category")?Number(data.get("category")):null};if(body.status==="future"){if(!data.get("scheduled_at"))throw new Error("زمان انتشار را انتخاب کنید.");body.scheduled_at=new Date(data.get("scheduled_at")).toISOString();}await api(`/articles/${form.dataset.articleId}/sync`,{method:"POST",body});closeModal();await refresh();toast("درخواست انتشار ثبت شد؛ نتیجه را در گزارش فعالیت‌ها ببینید.");}
  } catch(err) {if($("#modal").open)modalError(err.message);else toast(err.message,true);}
  finally {if(submit)submit.disabled=false;}
});

document.addEventListener("input",event=>{if(event.target.id==="article-search"){state.search=event.target.value;$("#article-list").innerHTML=articleList();}});
document.addEventListener("change",event=>{if(event.target.id==="publish-status")$("#schedule-wrap").hidden=event.target.value!=="future";});
document.addEventListener("keydown",event=>{if(event.key==="Enter" && event.target.matches("[data-article]")){event.preventDefault();event.target.click();}});
$("#modal").addEventListener("click",event=>{if(event.target===$("#modal")) {const box=$("#modal").getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)closeModal();}});
$("#modal").addEventListener("cancel",()=>{state.editor=null;});
window.addEventListener("hashchange",()=>{render();window.scrollTo(0,0);});
window.addEventListener("beforeunload",event=>{if(state.editor&&$("#edit-content")&&editorDirty()){event.preventDefault();event.returnValue="";}});

(async()=>{try{state.bootstrap=await api("/bootstrap");await refresh();}catch(err){$("#main").innerHTML=empty("ارتباط با برنامه برقرار نشد",e(err.message),'<button class="button" data-action="reload">تلاش دوباره</button>',"globe");$("[data-action=reload]")?.addEventListener("click",()=>location.reload());}})();
