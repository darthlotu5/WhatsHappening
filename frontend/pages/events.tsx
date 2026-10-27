import {
  Button,
  Card,
  CardContent,
  CircularProgress,
  TextField,
} from "@mui/material";
import { LocationOnOutlined, TodayOutlined } from "@mui/icons-material";
import Head from "next/head";
import React, { useEffect, useState } from "react";
import FetchError from "../components/FetchError";
import TopAppbar from "../components/TopAppbar";
import { EventDetails } from "../types/event";
import { API_URL, DEFAULT_ERR_MESSAGE } from "../utils/constants";

/** Events Page */
const Events = () => {
  const [fetching, setFetching] = useState(false);
  const [fetchErr, setFetchErr] = useState({
    error: false,
    message: "",
  });
  const [events, setEvents] = useState<EventDetails[]>([]);

  useEffect(() => {
    setFetching(true);
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
  return (
    <>
      <Head>
        <title>Events</title>
      </Head>
      <TopAppbar />
      {fetching ? (
        <div className="container mt-4 mx-auto">
          <CircularProgress />
        </div>
      ) : fetchErr.error ? (
        <FetchError errorMessage={fetchErr.message} reload />
      ) : (
        <div className="py-4 bg-gray-100">
          <div className="container mx-auto mb-4">
            <Card>
              <CardContent>
                <form className="flex gap-3">
                  <TextField
                    size="small"
                    variant="outlined"
                    name="name"
                    placeholder="Event Name"
                  />

                  <TextField
                    size="small"
                    variant="outlined"
                    name="date"
                    type="date"
                    placeholder="Event Date"
                  />

                  <TextField
                    size="small"
                    variant="outlined"
                    name="location"
                    placeholder="Event Location"
                  />

                  <Button variant="contained" color="primary">
                    Filter
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>
          <div className="container mx-auto grid grid-cols-4 gap-4">
            {events.map((event, idx) => (
              <a
                href={event.url}
                key={idx}
                target="_blank"
                rel="noreferrer noopener"
              >
                <Card>
                  <img
                    src={event.banner_url}
                    alt=""
                    className="object-cover h-64 w-full "
                  />
                  <CardContent>
                    <div className="grid gap-2">
                      <p className="text-lg font-medium">{event.name}</p>

                      <div className="flex gap-1 items-center text-gray-700">
                        <TodayOutlined />
                        <p className="text-sm">
                          {new Date(event.start_date * 1000).toDateString()}
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
      )}
    </>
  );
};

export default Events;
