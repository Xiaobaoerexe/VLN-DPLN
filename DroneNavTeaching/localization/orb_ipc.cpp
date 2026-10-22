#include <System.h>
#include <Atlas.h>
#include <MapPoint.h>
#include <Map.h>
#include <opencv2/core.hpp>
#include <unistd.h>
#include <cstdint>
#include <chrono>
#include <iostream>
#include <sstream>
#include <iomanip>
#include <vector>
#include <cmath>
static bool readAll(void* p,size_t n){ auto q=(char*)p; while(n){ssize_t k=::read(0,q,n);if(k<=0)return false;q+=k;n-=k;}return true;}
static bool writeAll(const void* p,size_t n){auto q=(const char*)p;while(n){ssize_t k=::write(1,q,n);if(k<=0)return false;q+=k;n-=k;}return true;}
static void reply(const std::string& s){uint32_t n=s.size();writeAll("OSR3",4);writeAll(&n,4);writeAll(s.data(),s.size());}
int main(int argc,char**argv){
 if(argc!=3)return 2;
 // Upstream logs use cout; reserve raw fd 1 exclusively for framed protocol.
 std::cout.rdbuf(std::cerr.rdbuf());
 ORB_SLAM3::System slam(argv[1],argv[2],ORB_SLAM3::System::RGBD,false);
 reply("{\"ready\":true}");
 for(;;){
  char magic[4];uint32_t w,h;double t;
  if(!readAll(magic,4))break;
  if(std::string(magic,4)!="OSF3" || !readAll(&w,4)||!readAll(&h,4)||!readAll(&t,8))break;
  if(w>4096||h>4096||!w||!h)break;
  cv::Mat rgb(h,w,CV_8UC3),dep(h,w,CV_32FC1);
  if(!readAll(rgb.data,w*h*3)||!readAll(dep.data,w*h*4))break;
  auto start=std::chrono::steady_clock::now();
  auto Tcw=slam.TrackRGBD(rgb,dep,t);
  int state=slam.GetTrackingState();
  auto Twc=Tcw.inverse().matrix();
  auto atlas=slam.GetTeachingAtlas();auto map=atlas->GetCurrentMap();
  auto points=atlas->GetAllMapPoints();
  std::ostringstream out;out<<std::setprecision(9)<<"{\"state\":"<<state<<",\"map_id\":"<<(map?long(map->GetId()):-1)<<",\"Twc\":[";
  for(int r=0;r<4;r++)for(int c=0;c<4;c++){if(r||c)out<<',';out<<Twc(r,c);}
  out<<"],\"points\":[";bool first=true;size_t count=0;
  // Limit display payload only; full map remains in ORB-SLAM3.
  size_t stride=std::max(size_t(1),points.size()/2500);
  for(size_t i=0;i<points.size();i+=stride){auto p=points[i];if(!p||p->isBad())continue;auto v=p->GetWorldPos();if(!v.allFinite())continue;
    if(!first)out<<',';first=false;out<<'['<<v.x()<<','<<v.y()<<','<<v.z()<<']';count++;
  }
  double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
  out<<"],\"map_point_count\":"<<points.size()<<",\"tracked_point_count\":"<<slam.GetTrackedMapPoints().size()<<",\"latency_ms\":"<<ms<<'}';reply(out.str());
 }
 slam.Shutdown();return 0;
}
