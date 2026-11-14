# GIF Recording Quick Reference Card

**Print this and keep next to your monitor while recording!**

---

## 🎬 Recording Settings
- **Resolution**: 1280x720 (720p)
- **Frame Rate**: 15 fps
- **Duration**: 2:05 (125 seconds)
- **Tool**: QuickTime or Kap

---

## 📋 15-Scene Checklist

| Time | Scene | Action | Overlay Text |
|------|-------|--------|--------------|
| 0:00-0:08 | Main Interface | Type query: "Show me all products with low inventory" | "From Intent to Autonomy" |
| 0:08-0:18 | Live Execution | Watch strategic planning → tactical execution | "From Guesswork to Clarity" |
| 0:18-0:28 | IFOC Profiles | Demo @CHAT and @FOCUS tags | "From Ideation to Operationalization" |
| 0:28-0:35 | MCP Servers | Anatomy → MCP Servers | - |
| 0:35-0:43 | LLM Configs | Show providers + model filter toggle | "Multi-Provider Intelligence" |
| 0:43-0:52 | Profiles | Show 4 IFOC classes with colors/icons | - |
| 0:52-1:02 | RAG Collections | Generate 50 questions from template | "From $$ to ¢¢¢" |
| 1:02-1:10 | Knowledge Repos | Upload docs with semantic chunking | "Zero hallucination guarantee" |
| 1:10-1:18 | Marketplace | Browse, install collection | "Collaborative Intelligence" |
| 1:18-1:25 | Agent Packs | Show pack contents + export | - |
| 1:25-1:33 | Administration | User management + tiers | - |
| 1:33-1:42 | Analytics | Cost tracking dashboard | "From Hidden Costs to Visibility" |
| 1:42-1:50 | Sovereignty | Champion Cases retrieval | "From Data Exposure to Sovereignty" |
| 1:50-1:58 | Efficiency | Show 60% token savings | "60% Cost Reduction" |
| 1:58-2:05 | Closing | Full interface tour + logo | "uderia.com" |

---

## 🎯 Test Queries to Pre-Type

```
1. Show me all products with low inventory
2. @CHAT What is the capital of France?
3. @FOCUS What does our policy say about refunds?
4. Analyze customer churn by segment
```

---

## ✅ Pre-Recording Checklist

- [ ] Clean database with demo data
- [ ] 3-4 sessions with names visible
- [ ] All 7 providers configured
- [ ] Sample RAG collections installed
- [ ] Sample knowledge repos with docs
- [ ] Browser at 100% zoom
- [ ] Close extra tabs/extensions
- [ ] QuickTime ready (Cmd+Ctrl+N)

---

## 🎬 Recording Flow

1. **Start app**: `python -m trusted_data_agent.main`
2. **Open browser**: http://localhost:5050
3. **Login**: admin / admin
4. **Start QuickTime**: File → New Screen Recording
5. **Select browser window only**
6. **Count down**: 3... 2... 1... Record!
7. **Follow 15 scenes** (slow, deliberate movements)
8. **Save**: File → Save as `recording.mov`

---

## 🔧 Post-Recording Commands

```bash
# Convert to optimized GIF
ffmpeg -i recording.mov \
  -vf "fps=15,scale=1280:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
  -loop 0 \
  AppOverview_raw.gif

# Optimize
gifsicle -O3 --lossy=80 --colors 256 \
  AppOverview_raw.gif -o images/AppOverview.gif

# Check size
du -h images/AppOverview.gif
```

---

## 💡 Pro Tips

- **Slow cursor** - Move at 50% normal speed
- **Pause on elements** - 1-2 sec per important button
- **Practice first** - Do a dry run without recording
- **One scene at a time** - Can splice together later
- **Read aloud** - Helps maintain pacing
- **Save often** - QuickTime can crash

---

## 📏 Timing Markers

| Minute Mark | What Should Be Showing |
|-------------|------------------------|
| 0:30 | Should be at MCP Servers panel |
| 1:00 | Should be at RAG Collections |
| 1:30 | Should be at Analytics dashboard |
| 2:00 | Should be at closing sequence |

---

## 🚨 Common Mistakes to Avoid

- ❌ Cursor moving too fast
- ❌ Not pausing on important UI elements
- ❌ Typing too quickly (hard to follow)
- ❌ Forgetting to show Live Status window updates
- ❌ Not demonstrating @TAG syntax
- ❌ Skipping profile color/icon indicators
- ❌ Recording entire screen instead of browser window
- ❌ Having errors/warnings visible in UI

---

## 📞 Emergency Troubleshooting

**If QuickTime crashes:**
- Restart and try Kap instead: `brew install --cask kap`

**If recording is choppy:**
- Lower frame rate to 10 fps
- Close all other applications
- Disable browser extensions

**If GIF is too large (>25MB):**
```bash
# Reduce colors
gifsicle -O3 --lossy=100 --colors 128 input.gif -o output.gif

# Or reduce frame rate
ffmpeg -i input.gif -vf "fps=10" output.gif
```

**If scenes are out of order:**
- Can record separately and concatenate:
```bash
ffmpeg -i scene1.mov -i scene2.mov \
  -filter_complex "[0:v][1:v]concat=n=2:v=1[outv]" \
  -map "[outv]" combined.mov
```

---

**Good luck! You've got this! 🎬**

**See full details**: [docs/GIF_STORYBOARD.md](GIF_STORYBOARD.md)
