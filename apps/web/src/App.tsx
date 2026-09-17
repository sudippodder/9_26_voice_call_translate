import React from "react";
import { useCallStore } from "@/store/callStore";
import { HomeScreen } from "@/screens/HomeScreen";
import { CallScreen } from "@/screens/CallScreen";
import { IncomingCallPoller } from "@/screens/IncomingCallScreen";

const App: React.FC = () => {
  const callState = useCallStore((s) => s.callState);
  const showCallScreen = callState !== "IDLE" && callState !== "ENDED";

  return (
    <div className="min-h-screen">
      {showCallScreen ? (
        <CallScreen />
      ) : (
        <>
          <HomeScreen />
          {/* Polls for incoming calls while on Home screen */}
          <IncomingCallPoller />
        </>
      )}
    </div>
  );
};

export default App;
