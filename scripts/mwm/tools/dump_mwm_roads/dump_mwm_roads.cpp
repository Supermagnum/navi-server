// Dump routable road geometries (+ optional wetland area triangles) from one .mwm.
// Built against a CoMaps omim-build-release tree (see build-dump-mwm-roads.sh).
//
// Roads JSONL (one feature per line):
//   {"feature_id":N,"points":[{"lat":..,"lon":..,"alt_m":N},...],"types":["highway-…",…]}
//
// Wetlands JSONL (when --out-wetlands is set):
//   {"feature_id":N,"class":1|2,"subtype":"bog"|…|"",
//    "triangles":[[[lat,lon],[lat,lon],[lat,lon]],…]}
// Soft=1 (bog/fen/untyped); Hard=2 (swamp/reedbed); marsh/other omitted (not emitted).
//
// Usage:
//   dump_mwm_roads --mwm PATH --resources_path PATH [--out PATH|-]
//                  [--out-wetlands PATH]

#include "indexer/altitude_loader.hpp"
#include "indexer/classificator.hpp"
#include "indexer/classificator_loader.hpp"
#include "indexer/feature.hpp"
#include "indexer/feature_data.hpp"
#include "indexer/feature_processor.hpp"
#include "indexer/ftypes_matcher.hpp"
#include "indexer/mwm_set.hpp"

#include "platform/local_country_file.hpp"
#include "platform/platform.hpp"

#include "geometry/mercator.hpp"

#include "base/logging.hpp"

#include <fstream>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace
{
void PrintUsage()
{
  std::cerr << "Usage: dump_mwm_roads --mwm PATH --resources_path PATH "
               "[--out PATH] [--out-wetlands PATH]\n";
}

bool StartsWith(std::string_view s, std::string_view prefix)
{
  return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

// Soft=1, Hard=2, omit=0. Matches Navi / annotate-wetland-edges policy.
// string_bog is mapped to bog in CoMaps mapcss (natural-wetland-bog).
uint8_t ClassifyWetlandType(std::string const & readable)
{
  if (readable == "natural-wetland" || readable == "natural-wetland-bog" ||
      readable == "natural-wetland-fen")
    return 1;
  if (readable == "natural-wetland-swamp" || readable == "natural-wetland-reedbed")
    return 2;
  // marsh, mangrove, saltmarsh, tidalflat, and unknown subtypes omitted
  if (StartsWith(readable, "natural-wetland"))
    return 0;
  return 0;
}

std::string WetlandSubtype(std::string const & readable)
{
  static std::string const kPrefix = "natural-wetland-";
  if (readable == "natural-wetland")
    return "";
  if (StartsWith(readable, kPrefix))
    return readable.substr(kPrefix.size());
  return "";
}

bool IsWetlandReadable(std::string const & readable)
{
  return StartsWith(readable, "natural-wetland");
}
}  // namespace

int main(int argc, char ** argv)
{
  std::string mwmPath;
  std::string resourcesPath;
  std::string outPath;
  std::string outWetlandsPath;

  for (int i = 1; i < argc; ++i)
  {
    std::string const arg = argv[i];
    auto need = [&](std::string & dst) {
      if (i + 1 >= argc)
      {
        PrintUsage();
        exit(2);
      }
      dst = argv[++i];
    };
    if (arg == "--mwm")
      need(mwmPath);
    else if (arg == "--resources_path")
      need(resourcesPath);
    else if (arg == "--out")
      need(outPath);
    else if (arg == "--out-wetlands")
      need(outWetlandsPath);
    else if (arg == "--help" || arg == "-h")
    {
      PrintUsage();
      return 0;
    }
    else
    {
      std::cerr << "unknown arg: " << arg << "\n";
      PrintUsage();
      return 2;
    }
  }

  if (mwmPath.empty() || resourcesPath.empty())
  {
    PrintUsage();
    return 2;
  }

  GetPlatform().SetResourceDir(resourcesPath);
  GetPlatform().SetWritableDirForTests(resourcesPath);
  classificator::Load();

  platform::LocalCountryFile const localFile = platform::LocalCountryFile::MakeTemporary(mwmPath);
  MwmValue mwmValue(localFile);
  feature::AltitudeLoaderCached altitudes(mwmValue);
  Classificator const & classifRef = classif();

  std::ostream * out = &std::cout;
  std::ofstream file;
  if (!outPath.empty() && outPath != "-")
  {
    file.open(outPath);
    if (!file)
    {
      std::cerr << "failed to open " << outPath << "\n";
      return 1;
    }
    out = &file;
  }

  std::ofstream wetFile;
  std::ostream * wetOut = nullptr;
  if (!outWetlandsPath.empty())
  {
    wetFile.open(outWetlandsPath);
    if (!wetFile)
    {
      std::cerr << "failed to open " << outWetlandsPath << "\n";
      return 1;
    }
    wetOut = &wetFile;
  }

  size_t roads = 0;
  size_t wetlands = 0;
  feature::ForEachFeature(mwmPath, [&](FeatureType & ft, uint32_t id) {
    feature::TypesHolder types(ft);

    if (wetOut != nullptr && types.GetGeomType() == feature::GeomType::Area)
    {
      uint8_t bestClass = 0;
      std::string subtype;
      for (uint32_t t : types)
      {
        std::string const readable = classifRef.GetReadableObjectName(t);
        if (!IsWetlandReadable(readable))
          continue;
        uint8_t const cls = ClassifyWetlandType(readable);
        if (cls == 0)
          continue;
        if (cls >= bestClass)
        {
          bestClass = cls;
          subtype = WetlandSubtype(readable);
        }
      }
      if (bestClass != 0)
      {
        ft.ParseTriangles(FeatureType::BEST_GEOMETRY);
        size_t const nTri = ft.GetTrgVerticesCount(FeatureType::BEST_GEOMETRY);
        if (nTri >= 3)
        {
          *wetOut << "{\"feature_id\":" << id << ",\"class\":" << int(bestClass)
                  << ",\"subtype\":\"" << subtype << "\",\"triangles\":[";
          bool firstTri = true;
          ft.ForEachTriangle(
              [&](m2::PointD const & a, m2::PointD const & b, m2::PointD const & c) {
                auto emit = [&](m2::PointD const & p) {
                  auto const ll = mercator::ToLatLon(p);
                  *wetOut << "[" << ll.m_lat << "," << ll.m_lon << "]";
                };
                if (!firstTri)
                  *wetOut << ",";
                firstTri = false;
                *wetOut << "[";
                emit(a);
                *wetOut << ",";
                emit(b);
                *wetOut << ",";
                emit(c);
                *wetOut << "]";
              },
              FeatureType::BEST_GEOMETRY);
          *wetOut << "]}\n";
          ++wetlands;
        }
      }
    }

    if (!ftypes::IsWayChecker::Instance()(types))
      return;
    ft.ParseGeometry(FeatureType::BEST_GEOMETRY);
    size_t const n = ft.GetPointsCount();
    if (n == 0)
      return;

    auto const alts = altitudes.GetAltitudes(id, n);
    *out << "{\"feature_id\":" << id << ",\"points\":[";
    for (size_t i = 0; i < n; ++i)
    {
      auto const ll = mercator::ToLatLon(ft.GetPoint(i));
      int const alt = (i < alts.size()) ? static_cast<int>(alts[i]) : 0;
      if (i)
        *out << ",";
      *out << "{\"lat\":" << ll.m_lat << ",\"lon\":" << ll.m_lon << ",\"alt_m\":" << alt << "}";
    }
    *out << "],\"types\":[";
    bool firstType = true;
    for (uint32_t t : types)
    {
      if (!firstType)
        *out << ",";
      firstType = false;
      *out << "\"" << classifRef.GetReadableObjectName(t) << "\"";
    }
    *out << "]}\n";
    ++roads;
  });

  LOG(LINFO, ("dumped", roads, "road features and", wetlands, "wetland areas from", mwmPath));
  return 0;
}
