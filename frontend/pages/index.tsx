import { Card, CardContent, CircularProgress } from "@mui/material";
import { LocationOnOutlined, TodayOutlined } from "@mui/icons-material";
import { useUser } from "@clerk/nextjs";
import Head from "next/head";
import React, { useEffect, useState } from "react";
import FetchError from "../components/FetchError";
import TopAppbar from "../components/TopAppbar";
import { EventDetails } from "../types/event";
import { API_URL, DEFAULT_ERR_MESSAGE } from "../utils/constants";
import { eventDate } from "../utils/eventDate";

/** Home Page */
const Index = () => {
  const { isLoaded, isSignedIn, user } = useUser();
  const [fetching, setFetching] = useState(false);
  const [fetchingR, setFetchingR] = useState(false);
  const [fetchErr, setFetchErr] = useState({
    error: false,
    message: "",
  });
  const [events, setEvents] = useState<EventDetails[]>([]);
  const [rEvents, setRevents] = useState<EventDetails[]>([]);

  useEffect(() => {
    setFetching(true);
    // Fetch Events

    fetch(API_URL, {
      mode: "cors",
      method: "GET",
    })
      .then((res) => {
        if (res.ok) {
          return res.json();
        }
        throw new Error();
      })
      .then((res) => setEvents(res["events"]))
      .catch((err) =>
        setFetchErr({ error: true, message: DEFAULT_ERR_MESSAGE })
      )
      .finally(() => setFetching(false));

  }, []);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setRevents([]);
      return;
    }
    setFetchingR(true);
    fetch(`${API_URL}/recommended`, {
      mode: "cors",
      method: "GET",
    })
      .then((res) => {
        if (res.ok) {
          return res.json();
        }
        throw new Error();
      })
      .then((res) => setRevents(res["events"]))
      .catch((err) =>
        setFetchErr({ error: true, message: DEFAULT_ERR_MESSAGE })
      )
      .finally(() => setFetchingR(false));
  }, [isLoaded, isSignedIn, user?.id]);

  const upcomingEvents = [...events].splice(0, 4);
  const popularEvents = [...rEvents].splice(0, 4);

  return (
    <>
      <Head>
        <title>Home</title>
      </Head>
      <TopAppbar />
      {fetching || fetchingR ? (
        <div className="container mt-4 mx-auto">
          <CircularProgress />
        </div>
      ) : fetchErr.error ? (
        <FetchError errorMessage={fetchErr.message} reload />
      ) : (
        <>
          <div className="bg-gray-100">
            <div className="container mx-auto pt-4 pb-6">
              <h6 className="text-lg mb-2">Upcoming Events</h6>
              {events.length === 0 && <p>No events found yet. Discovery is in progress.</p>}
              <div className="grid grid-cols-4 gap-3">
                {upcomingEvents.map((event) => (
                  <Card key={event.url}>
                    <CardContent>
                      <div className="grid gap-2">
                        <p className="font-medium">{event.name}</p>

                        <div className="flex gap-1 items-center text-gray-700">
                          <TodayOutlined />
                          <p className="text-sm">
                            {eventDate(event.start_date, event.date_label)}
                          </p>
                        </div>

                        <div className="flex gap-1 items-center text-gray-600">
                          <LocationOnOutlined />
                          <p className="text-sm">{event.location || "Location not provided"}</p>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          </div>

          <div className="container mx-auto py-4">
            <h6 className="text-lg mb-2">Recommended Events</h6>
            {isLoaded && !isSignedIn && <p>Sign in to see recommended events.</p>}
            <div className="grid grid-cols-4 gap-3">
              {rEvents.map((event, idx) => (
                <a
                  href={event.url}
                  key={idx}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  <Card>
                    <img
                      src={event.banner_url || undefined}
                      loading="lazy"
                      alt=""
                      className="object-cover h-64 w-full "
                    />
                    <CardContent>
                      <div className="grid gap-2">
                        <p className="text-lg font-medium">{event.name}</p>

                        <div className="flex gap-1 items-center text-gray-700">
                          <TodayOutlined />
                          <p className="text-sm">
                            {eventDate(event.start_date, event.date_label)}
                          </p>
                        </div>

                        <div className="flex gap-1 items-center text-gray-600">
                          <LocationOnOutlined />
                          <p className="text-sm">{event.location || "Location not provided"}</p>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                </a>
              ))}
            </div>
          </div>

          <div className="container mx-auto py-4">
            <h6 className="text-lg mb-2">Popular Events</h6>
            <div className="grid grid-cols-4 gap-3">
              {popularEvents.map((event, idx) => (
                <a
                  href={event.url}
                  key={idx}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  <Card>
                    <img
                      src={event.banner_url || undefined}
                      loading="lazy"
                      alt=""
                      className="object-cover h-64 w-full "
                    />
                    <CardContent>
                      <div className="grid gap-2">
                        <p className="text-lg font-medium">{event.name}</p>

                        <div className="flex gap-1 items-center text-gray-700">
                          <TodayOutlined />
                          <p className="text-sm">
                            {eventDate(event.start_date, event.date_label)}
                          </p>
                        </div>

                        <div className="flex gap-1 items-center text-gray-600">
                          <LocationOnOutlined />
                          <p className="text-sm">{event.location}</p>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                </a>
              ))}
            </div>
          </div>
        </>
      )}
    </>
  );
};

export default Index;
