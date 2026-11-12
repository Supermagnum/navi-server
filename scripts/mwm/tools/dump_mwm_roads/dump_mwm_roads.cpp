// Dump routable road geometries + CoMaps altitudes from one .mwm.
// Built against a CoMaps omim-build-release tree (see build-dump-mwm-roads.sh).
//
// Output JSONL (one feature per line):
//   {"feature_id":N,"points":[{"lat":..,"lon":..,"alt_m":N},...]}
//
// Usage:
//   dump_mwm_roads --mwm PATH --resources_path PATH [--out PATH|-]

#include "indexer/altitude_loader.hpp"
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

namespace
{
void PrintUsage()
{
  std::cerr << "Usage: dump_mwm_roads --mwm PATH --resources_path PATH [--out PATH]\n";
}
}  // namespace

int main(int argc, char ** argv)
{
  std::string mwmPath;
  std::string resourcesPath;
  std::string outPath;

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

  size_t roads = 0;
  feature::ForEachFeature(mwmPath, [&](FeatureType & ft, uint32_t id) {
    if (!ftypes::IsWayChecker::Instance()(feature::TypesHolder(ft)))
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
    *out << "]}\n";
    ++roads;
  });

  LOG(LINFO, ("dumped", roads, "road features from", mwmPath));
  return 0;
}
