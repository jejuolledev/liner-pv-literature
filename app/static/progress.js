// 검토가 도는 동안 진행 줄을 1.5초마다 새로 읽는다.
(function () {
  var box = document.getElementById("progress");
  if (!box) return;
  var id = box.dataset.run;
  function set(i, v) { var e = document.getElementById(i); if (e) e.textContent = v; }
  function tick() {
    fetch("/api/run/" + id).then(function (r) { return r.json(); }).then(function (d) {
      var s = d.stats || {}, l = d.ledger || {};
      set("p-search", l.scholar_calls || 0); set("p-found", s.found || 0); set("p-seen", s.already_seen || 0);
      set("p-new", s.new || 0); set("p-cand", (s.candidates || 0) + (s.check || 0) + (s.errors || 0)); set("p-cost", "$" + (l.cost_usd || 0).toFixed(3));
      set("p-phase", (s.phase || "") + (s.judged ? ", AI가 읽은 논문 " + s.judged + "/" + s.new : "") + (s.elapsed_sec ? ", " + s.elapsed_sec + "초" : ""));
      set("p-status", { running: "진행 중", done: "끝남", failed: "실패", partial: "일부 실패" }[d.status] || d.status);
      var pct = d.status === "done" ? 100 : (s.new ? 20 + 80 * (s.judged || 0) / s.new : Math.min(20, (l.scholar_calls || 0) * 2));
      document.getElementById("p-fill").style.width = pct + "%";
      var log = document.getElementById("p-log"); log.innerHTML = "";
      (d.log || []).forEach(function (t) { var li = document.createElement("li"); li.textContent = t; log.appendChild(li); });
      if (d.status === "running") setTimeout(tick, 1500); else if (box.dataset.status === "running") location.reload();
    }).catch(function () { setTimeout(tick, 3000); });
  }
  tick();
})();
