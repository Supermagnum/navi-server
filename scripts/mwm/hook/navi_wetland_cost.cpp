// Sectioned NVSC wetland sidecar loader + cost multipliers for routes_builder.
#include "routing/navi_wetland_cost.hpp"

#include "base/logging.hpp"

#include <cstring>
#include <cstdlib>
#include <fstream>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include <zstd.h>

namespace navi_spike
{
namespace
{
constexpr char const kMagic[4] = {'N', 'V', 'S', 'C'};
constexpr char const kWetlandSection[] = "wetland_edges";
constexpr double kSoftMult = 5.0;
constexpr double kHardMult = 1.0e6;

struct Key
{
  uint32_t featureId = 0;
  uint16_t segmentIdx = 0;
  uint8_t direction = 0;
  bool operator==(Key const & o) const
  {
    return featureId == o.featureId && segmentIdx == o.segmentIdx && direction == o.direction;
  }
};

struct KeyHash
{
  size_t operator()(Key const & k) const
  {
    return (size_t(k.featureId) * 1315423911u) ^ (size_t(k.segmentIdx) << 1) ^ k.direction;
  }
};

std::mutex g_mu;
std::unordered_map<Key, WetlandClass, KeyHash> g_table;
std::string g_loadedStem;
bool g_enabledChecked = false;
bool g_enabled = false;

std::string SidecarDir()
{
  char const * d = std::getenv("NAVI_WETLAND_SIDECAR_DIR");
  return d ? std::string(d) : std::string();
}

bool ReadFile(std::string const & path, std::vector<uint8_t> & out)
{
  std::ifstream in(path, std::ios::binary);
  if (!in)
    return false;
  in.seekg(0, std::ios::end);
  auto const n = static_cast<std::streamoff>(in.tellg());
  if (n <= 0)
    return false;
  out.resize(static_cast<size_t>(n));
  in.seekg(0);
  in.read(reinterpret_cast<char *>(out.data()), n);
  return static_cast<bool>(in);
}

bool ReadU16Str(std::vector<uint8_t> const & body, size_t & off, std::string & out)
{
  if (off + 2 > body.size())
    return false;
  uint16_t len = 0;
  std::memcpy(&len, body.data() + off, 2);
  off += 2;
  if (off + len > body.size())
    return false;
  out.assign(reinterpret_cast<char const *>(body.data() + off), len);
  off += len;
  return true;
}

bool LoadWetlandRecords(std::vector<uint8_t> const & section)
{
  if (section.size() < 4)
    return false;
  uint32_t n = 0;
  std::memcpy(&n, section.data(), 4);
  size_t off = 4;
  if (off + size_t(n) * 8 > section.size())
    return false;
  g_table.clear();
  g_table.reserve(n);
  for (uint32_t i = 0; i < n; ++i)
  {
    uint32_t fid = 0;
    uint16_t seg = 0;
    uint8_t dir = 0;
    uint8_t cls = 0;
    std::memcpy(&fid, section.data() + off, 4);
    std::memcpy(&seg, section.data() + off + 4, 2);
    dir = section[off + 6];
    cls = section[off + 7];
    off += 8;
    if (cls != 1 && cls != 2)
      continue;
    g_table[Key{fid, seg, dir}] = static_cast<WetlandClass>(cls);
  }
  return true;
}

bool LoadSectionedSidecar(std::vector<uint8_t> const & logical)
{
  if (logical.size() < 12 || std::memcmp(logical.data(), kMagic, 4) != 0)
    return false;
  uint32_t version = 0;
  std::memcpy(&version, logical.data() + 4, 4);
  if (version != 1)
    return false;
  size_t off = 8;
  std::string tag;
  std::string sha;
  if (!ReadU16Str(logical, off, tag) || !ReadU16Str(logical, off, sha))
    return false;
  if (off + 32 + 8 + 4 > logical.size())
    return false;
  off += 32;  // mwm sha256
  off += 8;   // mwm size
  uint32_t sectionCount = 0;
  std::memcpy(&sectionCount, logical.data() + off, 4);
  off += 4;

  struct Sec
  {
    std::string name;
    uint64_t uncompressed = 0;
    uint64_t compressed = 0;
    uint64_t offset = 0;
  };
  std::vector<Sec> secs;
  secs.reserve(sectionCount);
  for (uint32_t i = 0; i < sectionCount; ++i)
  {
    Sec s;
    if (!ReadU16Str(logical, off, s.name))
      return false;
    if (off + 8 + 8 + 32 + 8 > logical.size())
      return false;
    std::memcpy(&s.uncompressed, logical.data() + off, 8);
    off += 8;
    std::memcpy(&s.compressed, logical.data() + off, 8);
    off += 8;
    off += 32;  // section sha256
    std::memcpy(&s.offset, logical.data() + off, 8);
    off += 8;
    secs.push_back(std::move(s));
  }
  size_t const payloadBase = off;

  for (Sec const & s : secs)
  {
    if (s.name != kWetlandSection)
      continue;
    if (payloadBase + s.offset + s.compressed > logical.size())
      return false;
    uint8_t const * comp = logical.data() + payloadBase + static_cast<size_t>(s.offset);
    std::vector<uint8_t> raw(static_cast<size_t>(s.uncompressed));
    size_t const got = ZSTD_decompress(raw.data(), raw.size(), comp, static_cast<size_t>(s.compressed));
    if (ZSTD_isError(got) || got != raw.size())
      return false;
    if (!LoadWetlandRecords(raw))
      return false;
    LOG(LINFO, ("navi wetland sectioned sidecar loaded records=", g_table.size(),
                " generator=", tag));
    return true;
  }
  return false;
}

bool LoadFlatSpike2(std::vector<uint8_t> const & body)
{
  // Legacy spike2: NVSC + ver + sha256 + size + count + records (no gen strings).
  if (body.size() < 52 || std::memcmp(body.data(), kMagic, 4) != 0)
    return false;
  uint32_t version = 0;
  std::memcpy(&version, body.data() + 4, 4);
  if (version != 1)
    return false;
  size_t off = 4 + 4 + 32 + 8;
  uint32_t n = 0;
  std::memcpy(&n, body.data() + off, 4);
  off += 4;
  if (off + size_t(n) * 8 != body.size())
    return false;
  g_table.clear();
  g_table.reserve(n);
  for (uint32_t i = 0; i < n; ++i)
  {
    uint32_t fid = 0;
    uint16_t seg = 0;
    uint8_t dir = 0;
    uint8_t cls = 0;
    std::memcpy(&fid, body.data() + off, 4);
    std::memcpy(&seg, body.data() + off + 4, 2);
    dir = body[off + 6];
    cls = body[off + 7];
    off += 8;
    if (cls != 1 && cls != 2)
      continue;
    g_table[Key{fid, seg, dir}] = static_cast<WetlandClass>(cls);
  }
  LOG(LINFO, ("navi wetland flat spike2 sidecar loaded records=", g_table.size()));
  return true;
}

bool LoadSidecarBytes(std::vector<uint8_t> const & raw)
{
  unsigned long long const bound = ZSTD_getFrameContentSize(raw.data(), raw.size());
  if (bound == ZSTD_CONTENTSIZE_ERROR || bound == ZSTD_CONTENTSIZE_UNKNOWN || bound < 52)
    return false;
  std::vector<uint8_t> body(static_cast<size_t>(bound));
  size_t const got = ZSTD_decompress(body.data(), body.size(), raw.data(), raw.size());
  if (ZSTD_isError(got) || got < 52)
    return false;
  body.resize(got);
  // Prefer sectioned (client format); fall back to flat spike2 for old fixtures.
  if (LoadSectionedSidecar(body))
    return true;
  return LoadFlatSpike2(body);
}
}  // namespace

bool WetlandSidecar::Enabled()
{
  if (!g_enabledChecked)
  {
    g_enabled = !SidecarDir().empty();
    g_enabledChecked = true;
  }
  return g_enabled;
}

bool WetlandSidecar::EnsureLoadedForMwm(std::string const & mwmStem, std::string const & /*mwmPath*/)
{
  if (!Enabled())
    return false;
  std::lock_guard<std::mutex> lock(g_mu);
  if (g_loadedStem == mwmStem)
    return true;
  std::string path = SidecarDir();
  if (!path.empty() && path.back() != '/')
    path.push_back('/');
  path += mwmStem + ".navi-sidecar.v1.zst";
  std::vector<uint8_t> raw;
  if (!ReadFile(path, raw) || !LoadSidecarBytes(raw))
  {
    LOG(LWARNING, ("navi wetland sidecar load failed:", path));
    g_table.clear();
    g_loadedStem = mwmStem;
    return false;
  }
  g_loadedStem = mwmStem;
  return true;
}

WetlandClass WetlandSidecar::Lookup(uint32_t featureId, uint32_t segmentIdx, bool forward)
{
  if (!Enabled())
    return WetlandClass::None;
  {
    std::lock_guard<std::mutex> lock(g_mu);
    if (g_table.empty() && g_loadedStem.empty())
    {
      std::string dir = SidecarDir();
      if (!dir.empty() && dir.back() != '/')
        dir.push_back('/');
      for (char const * stem : {"Norway_Hedmark", "Norway_Oppland", "Norway_Central", "Norway_Southern"})
      {
        std::string path = dir + stem + ".navi-sidecar.v1.zst";
        std::vector<uint8_t> raw;
        if (ReadFile(path, raw) && LoadSidecarBytes(raw))
        {
          g_loadedStem = stem;
          break;
        }
      }
    }
  }
  Key key{featureId, static_cast<uint16_t>(segmentIdx), static_cast<uint8_t>(forward ? 1 : 0)};
  std::lock_guard<std::mutex> lock(g_mu);
  auto it = g_table.find(key);
  return it == g_table.end() ? WetlandClass::None : it->second;
}

double WetlandSidecar::CostMultiplier(WetlandClass c)
{
  switch (c)
  {
  case WetlandClass::SoftAvoid: return kSoftMult;
  case WetlandClass::HardAvoid: return kHardMult;
  case WetlandClass::None: return 1.0;
  }
  return 1.0;
}

}  // namespace navi_spike
