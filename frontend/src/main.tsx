import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./styles.css";
import Home from "./Home";
import HotelSite from "./guest/HotelSite";
import Login from "./staff/Login";
import StaffLayout from "./staff/Layout";
import Dashboard from "./staff/Dashboard";
import CalendarPage from "./staff/Calendar";
import BookingsPage from "./staff/Bookings";
import Inbox from "./staff/Inbox";
import RoomsPage from "./staff/Rooms";
import RatesPage from "./staff/Rates";
import KnowledgePage from "./staff/Knowledge";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/h/:slug" element={<HotelSite />} />
        <Route path="/staff/login" element={<Login />} />
        <Route path="/staff" element={<StaffLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="calendar" element={<CalendarPage />} />
          <Route path="bookings" element={<BookingsPage />} />
          <Route path="inbox" element={<Inbox />} />
          <Route path="inbox/:sid" element={<Inbox />} />
          <Route path="rooms" element={<RoomsPage />} />
          <Route path="rates" element={<RatesPage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
